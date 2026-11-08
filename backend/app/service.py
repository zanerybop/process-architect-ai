import hashlib
import time
import uuid
from datetime import datetime, timezone

from backend.app.audit import CLARIFICATION_CODES, audit
from backend.app.behavior import behavior_issues, validate_behavior
from backend.app.config import Settings
from backend.app.document_feedback import repair_context
from backend.app.document_prompt import schema_context
from backend.app.domain import Issue, PipelineError
from backend.app.graph import validate_graph
from backend.app.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from backend.app.repair_feedback import bounded_repair_feedback
from backend.app.semantic_review import SEMANTIC_RULES_VERSION, review_source
from backend.app.serialization import serialize
from backend.app.storage import Storage
from backend.app.xml_validation import validate_xml
from worker.interpreter import parse_calls


def merge_issues(*groups: list[Issue]) -> list[Issue]:
    values = [issue for group in groups for issue in group]
    missing_roles = {tuple(issue.element_ids) for issue in values if issue.code == "UNASSIGNED_ROLE"}
    result, seen = [], set()
    for issue in values:
        if issue.code == "ROLE_UNSPECIFIED" and tuple(issue.element_ids) in missing_roles:
            continue
        key = (issue.code, tuple(issue.element_ids), issue.message if not issue.element_ids else "")
        if key not in seen:
            result.append(issue)
            seen.add(key)
    return result


def source_quote_issues(calls: list[tuple[str, dict]], source: str) -> list[Issue]:
    """Inspect literal citation data, without executing any generated call."""
    issues = []
    for _, values in calls:
        quote, identifier = values.get("source_quote"), values.get("id")
        if isinstance(quote, str) and quote and quote not in source:
            issues.append(Issue(
                code="SOURCE_QUOTE_NOT_FOUND", message="Указанная цитата не совпадает с исходным описанием.",
                source_quote=quote[:4000],
                element_ids=[identifier] if isinstance(identifier, str) and len(identifier) <= 100 else [],
                suggested_action=(
                    "Скопируйте непрерывный фрагмент исходного текста дословно, включая имена и кавычки."
                ),
            ))
    return issues[:20]


class GenerationService:
    def __init__(self, settings: Settings, provider, runner, storage: Storage, progress=None):
        self.settings, self.provider, self.runner, self.storage = settings, provider, runner, storage
        self.progress = progress

    def _progress(self, phase, attempt=0):
        if self.progress:
            self.progress({"phase": phase, "attempt": attempt})

    async def generate(self, description: str, *, clarification_answers: list[dict] | None = None) -> dict:
        started = time.perf_counter()
        original_description = description
        answers = clarification_answers or []
        if answers:
            description += "\n\nОтветы аналитика (уточняют исходное описание):\n" + "\n".join(
                f"Вопрос: {item['question']}\nОтвет: {item['answer']}" for item in answers
            )
        if len(description) > 20_000:
            raise PipelineError("INPUT_LIMIT", "Описание вместе с ответами превышает 20 000 символов.")
        result = {
            "id": uuid.uuid4().hex,
            "mode": self.settings.app_mode,
            "status": "error",
            "python_code": None,
            "ir": None,
            "bpmn_xml": None,
            "checks": {key: "not_run" for key in ("code", "graph", "behavior", "xsd", "layout", "import")},
            "issues": [],
            "questions": [],
            "timings": {},
            "attempt_count": 0,
            "revision_id": uuid.uuid4().hex,
            "audit_stale": False,
            "source_context": {"description": original_description, "clarification_answers": answers},
            "semantic_review": None,
            "behavior": None,
        }
        feedback, raw_feedback, attempts = [], [], []
        metadata = {
            "mode": self.settings.app_mode,
            "provider": self.provider.provider,
            "model": self.provider.model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "attempts": attempts,
            "provider_profile": getattr(self.provider, "profile_metadata", None),
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            "schema_context_sha256": hashlib.sha256(schema_context(description).encode()).hexdigest(),
            "semantic_rules_version": SEMANTIC_RULES_VERSION,
            "compiler_profile": "bpmn-2.0.2-document",
            "compiler_version": "bpmn-compiler-v6-direct-data-objects",
            "generation_parameters": {
                "response_format": self.settings.llm_response_format,
                "max_tokens": self.settings.llm_max_tokens,
                "token_parameter": self.settings.llm_token_parameter,
                "reasoning_effort": self.settings.llm_reasoning_effort,
                "timeout_seconds": self.settings.llm_timeout_seconds,
                "max_repair_attempts": self.settings.llm_max_repair_attempts,
            },
        }
        for index in range(self.settings.llm_max_repair_attempts + 1):
            result["issues"] = []
            result["questions"] = []
            result.pop("summary", None)
            result["python_code"] = None
            result["ir"] = None
            result["bpmn_xml"] = None
            result["semantic_review"] = None
            result["behavior"] = None
            result["checks"] = {key: "not_run" for key in result["checks"]}
            attempt_started = time.perf_counter()
            result["attempt_count"] += 1
            attempt = {"number": index + 1, "usage": None, "error_codes": []}
            attempt["schema_context_sha256"] = hashlib.sha256(
                schema_context(description, feedback or None).encode()
            ).hexdigest()
            attempt["stage_seconds"] = {}
            attempts.append(attempt)
            repairable = False
            ir = None
            try:
                self._progress("model" if index == 0 else "repair", index + 1)
                stage_started = time.perf_counter()
                answer = await self.provider.generate(description, feedback=feedback or None)
                attempt["stage_seconds"]["model"] = round(time.perf_counter() - stage_started, 4)
                attempt["usage"] = answer.usage
                envelope = answer.envelope
                attempt["mode"] = envelope.mode
                if envelope.mode == "clarify":
                    result["status"] = "needs_clarification"
                    result["questions"] = [q.model_dump() for q in envelope.questions]
                    break
                if envelope.mode == "unsupported":
                    result["status"] = "unsupported"
                    result["issues"] = [
                        Issue(
                            code="UNSUPPORTED", category="model", severity="warning", message=reason
                        ).model_dump()
                        for reason in envelope.reasons
                    ]
                    break
                code = envelope.python_code
                attempt["python_code"] = code
                result["python_code"] = code
                result["summary"] = envelope.summary
                result["checks"] = {key: "not_run" for key in result["checks"]}
                repairable = True
                self._progress("code", index + 1)
                calls = parse_calls(code)
                result["checks"]["code"] = "passed"
                quote_errors = source_quote_issues(calls, description)
                if quote_errors:
                    raise PipelineError(quote_errors[0].code, quote_errors[0].message, issues=quote_errors)
                stage_started = time.perf_counter()
                self._progress("worker", index + 1)
                ir = await self.runner.run(code, description)
                attempt["stage_seconds"]["worker"] = round(time.perf_counter() - stage_started, 4)
                ir.assumptions = envelope.assumptions
                result["ir"] = ir.model_dump()
                self._progress("review", index + 1)
                issues = validate_graph(ir)
                errors = [issue for issue in issues if issue.severity == "error"]
                if errors:
                    result["checks"]["graph"] = "failed"
                    raise PipelineError(
                        errors[0].code, errors[0].message, errors[0].element_ids, issues=errors[:30]
                    )
                result["checks"]["graph"] = "passed"
                behavior = validate_behavior(ir)
                result["behavior"] = behavior.model_dump()
                result["checks"]["behavior"] = behavior.status
                behavior_errors = [item for item in behavior_issues(behavior) if item.severity == "error"]
                if behavior_errors:
                    raise PipelineError(
                        behavior_errors[0].code, behavior_errors[0].message, issues=behavior_errors[:30]
                    )
                issues = merge_issues(audit(ir), issues)
                if answers:
                    # The original statement of uncertainty is retained as history. Only an answer
                    # anchored to that exact original source fragment resolves this narrow warning.
                    issues = [issue for issue in issues if not (
                        issue.code == "SOURCE_UNCERTAINTY" and issue.source_quote
                        and any(issue.source_quote in item.get("source_quote", "") for item in answers)
                    )]
                unresolved = [issue for issue in issues if issue.code in CLARIFICATION_CODES]
                review = review_source(ir)
                result["semantic_review"] = review
                semantic_errors = [Issue.model_validate(item) for item in review["issues"]
                                   if item["severity"] == "error"]
                missing_evidence = [issue for issue in unresolved if issue.code in {
                    "ACTION_WITHOUT_SOURCE", "UNASSIGNED_ROLE", "UNACCEPTED_ASSUMPTION"
                }]
                if (ir.document is not None and missing_evidence
                        and index < self.settings.llm_max_repair_attempts
                        and not any(issue.code == "SOURCE_UNCERTAINTY" for issue in unresolved)):
                    # A model can omit evidence or misclassify a technical choice as an assumption.
                    # Require a new model answer; never silently remove assumptions or invent roles.
                    raise PipelineError(
                        missing_evidence[0].code, missing_evidence[0].message,
                        issues=merge_issues(missing_evidence, semantic_errors)[:30],
                    )
                if unresolved:
                    result["status"] = "needs_clarification"
                    result["issues"] = [issue.model_dump() for issue in issues]
                    result["questions"] = [
                        {
                            "question": issue.suggested_action or issue.message,
                            "source_quote": issue.source_quote or description[:200],
                        }
                        for issue in unresolved
                    ]
                    break
                if semantic_errors:
                    raise PipelineError(
                        semantic_errors[0].code, semantic_errors[0].message, issues=semantic_errors[:30]
                    )
                # Compiler/layout errors must not change the business graph through LLM repair.
                repairable = False
                self._progress("xml", index + 1)
                stage_started = time.perf_counter()
                xml = serialize(ir)
                attempt["stage_seconds"]["compiler"] = round(time.perf_counter() - stage_started, 4)
                stage_started = time.perf_counter()
                validation = validate_xml(xml)
                attempt["stage_seconds"]["xml_validation"] = round(time.perf_counter() - stage_started, 4)
                result["checks"].update(validation["checks"])
                result["behavior"] = validation.get("behavior", result["behavior"])
                result["issues"] = [
                    issue.model_dump()
                    for issue in merge_issues(
                        issues, [Issue.model_validate(item) for item in validation["issues"]]
                    )
                ]
                library_document = False
                if ir.document is not None:
                    from backend.app.bpmn_schema import catalog

                    library_document = not any(
                        catalog().derived_from(
                            item.attributes.get("xsi:type") or catalog().element_type(item.tag) or "",
                            base,
                        )
                        for item in ir.document.elements
                        for base in ("tProcess", "tCollaboration")
                    )
                if (any(result["checks"][gate] != "passed" for gate in ("xsd", "graph"))
                        or (result["checks"]["layout"] != "passed" and not (
                            library_document and result["checks"]["layout"] == "not_run"
                        ))):
                    raise PipelineError(
                        "COMPILER_VALIDATION", "Сформированный XML не прошёл технические проверки.",
                        issues=[Issue.model_validate(item) for item in validation["issues"]
                                if item.get("severity") == "error"],
                    )
                if result["checks"].get("behavior") == "failed":
                    raise PipelineError(
                        "BEHAVIOR_INVALID", "В сформированной модели найден поведенческий дефект."
                    )
                result["bpmn_xml"] = xml
                result["document_kind"] = "definitions" if library_document else "diagram"
                result["status"] = "ready"
                break
            except PipelineError as exc:
                issue = exc.issue
                result["issues"] = [item.model_dump() for item in (exc.issues or [issue])]
                attempt["error_codes"] = list(dict.fromkeys(
                    item.code for item in (exc.issues or [issue])
                ))
                if issue.code == "CODE_POLICY":
                    result["checks"]["code"] = "failed"
                if issue.code == "PROFILE_LIMIT":
                    result["status"] = "unsupported"
                    break
                if issue.code == "LLM_ENVELOPE":
                    repairable = True
                if issue.code in {
                    "RUNNER_UNAVAILABLE",
                    "WORKER_TIMEOUT",
                    "WORKER_FAILURE",
                    "WORKER_PROTOCOL",
                    "OUTPUT_LIMIT",
                    "WORKER_BUSY",
                }:
                    repairable = False
                if not repairable or index == self.settings.llm_max_repair_attempts:
                    break
                item = {"python_code": result["python_code"], "issues": result["issues"]}
                context = repair_context(ir, result["issues"])
                if context:
                    item["document_context"] = context
                raw_feedback.append(item)
                try:
                    feedback = bounded_repair_feedback(raw_feedback)
                except PipelineError as limit_error:
                    result["issues"].append(limit_error.issue.model_dump())
                    attempt["error_codes"].append(limit_error.issue.code)
                    break
            finally:
                attempt["elapsed_seconds"] = round(time.perf_counter() - attempt_started, 4)
                for key in ("ir", "behavior", "semantic_review", "checks", "issues"):
                    attempt[key] = result[key]
        result["timings"]["total_seconds"] = round(time.perf_counter() - started, 4)
        metadata.update(
            {
                "attempt_count": result["attempt_count"],
                "timings": result["timings"],
                "status": result["status"],
            }
        )
        self.storage.save(result, description, metadata)
        self._progress(result["status"], result["attempt_count"])
        return result
