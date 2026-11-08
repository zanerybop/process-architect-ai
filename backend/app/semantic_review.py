"""Source coverage and deliberately bounded semantic rules, not an LLM quality oracle."""

import re

from backend.app.audit import _verified
from backend.app.builder import TASK_TYPES
from backend.app.domain import Issue, ProcessIR

SEMANTIC_RULES_VERSION = "source-review-v4-event-intent"

DECISION = re.compile(r"\b(?:принима(?:ет|ют|ть)|приня(?:ть|л[аи]?))\s+решени\w*", re.I)
APPROVE = re.compile(r"\b(?:одобр\w+|утвержд\w+|утверд\w+|согласов\w+|согласу\w+|approv\w*)", re.I)
REJECT = re.compile(r"\b(?:отклон\w+|отказ\w+|reject\w*|refus\w*)", re.I)
COMPOUND_CONDITION = re.compile(
    r"\bесли\s+([^.!?\n,]{3,120}),\s*(?:но|и)\s+([^.!?\n,]{3,120}),",
    re.I,
)
NAMED_DATA = re.compile(
    r'\b(?:файл\w*|поле|поля|полю|документ\w*|отч[её]т\w*|форм[а-яё]+)\s+'
    r'(?:с\s+названием\s+)?(?:«(?P<angle>[^»\n]{1,120})»|"(?P<straight>[^"\n]{1,120})")',
    re.I,
)


def _words(text):
    return set(re.findall(r"[а-яёa-z]{2,}", text.lower().replace("ё", "е"))) - {
        "если",
        "когда",
        "или",
        "при",
        "для",
        "после",
        "это",
        "все",
        "будет",
        "но",
    }


def _compatible_words(expected, actual):
    # Conservative morphology for locating candidates, never a general semantic proof.
    return all(any(word[:5] == other[:5] for other in actual) for word in expected)


def _condition_enforced(ir, branch, expected):
    """A predicate may be on this branch or on every path leading to its gateway."""
    qualifying = {
        flow.id for flow in ir.flows if flow.kind == "sequence"
        and _compatible_words(expected, _words(f"{flow.name} {flow.condition or ''}"))
    }
    if branch.id in qualifying:
        return True
    process = next((node.process_id for node in ir.nodes if node.id == branch.source), None)
    starts = [node.id for node in ir.nodes if node.process_id == process and node.type == "startEvent"]
    if not starts or not qualifying:
        return False
    # Removing predicate-bearing edges must make the later gateway unreachable.
    adjacent = {}
    for flow in ir.flows:
        if flow.kind == "sequence" and flow.id not in qualifying:
            adjacent.setdefault(flow.source, []).append(flow.target)
    pending, visited = list(starts), set()
    while pending:
        node = pending.pop()
        if node == branch.source:
            return False
        if node not in visited:
            visited.add(node)
            pending.extend(adjacent.get(node, ()))
    return True


def review_source(ir: ProcessIR) -> dict:
    if ir.document is not None:
        from backend.app.document_intent import event_intent_issues
        from backend.app.document_requirements import required_construct_issues
        from backend.app.document_review import (
            associated_data_names,
            explicit_document_issues,
            source_projection,
        )

        projection = source_projection(ir)
        report = review_source(projection)
        linked_data = associated_data_names(ir)
        # A data declaration may live in a separate sentence from its use. With
        # an explicit cited owner, require that owner; without one, accept only
        # a typed association on an actual, source-cited task (not an orphan).
        cited_tasks = {
            item.element_id for item in ir.provenance if _verified(item, ir.source_text)
        } & {node.id for node in projection.nodes if node.type in TASK_TYPES}
        retained = []
        for issue in report["issues"]:
            named = NAMED_DATA.search(issue.get("source_quote") or "")
            name = (named.group("angle") or named.group("straight")) if named else None
            if (
                issue["code"] == "SOURCE_NAMED_DATA_OMITTED"
                and name
                and any(name in linked_data.get(identifier, set()) for identifier in (
                    issue["element_ids"] or cited_tasks
                ))
            ):
                continue
            retained.append(issue)
        report["issues"] = retained
        report["issues"].extend(issue.model_dump() for issue in explicit_document_issues(ir))
        report["issues"].extend(issue.model_dump() for issue in required_construct_issues(ir))
        report["issues"].extend(issue.model_dump() for issue in event_intent_issues(ir))
        report["status"] = "needs_review" if report["issues"] else "rules_passed"
        report["scope"] = "source_evidence_and_activity_wording"
        return report
    evidence = {item.element_id: item for item in ir.provenance if _verified(item, ir.source_text)}
    tasks = [node for node in ir.nodes if node.type in TASK_TYPES]
    issues = []
    coverage = []
    for index, match in enumerate(re.finditer(r"[^.!?\n]+(?:[.!?]|$)", ir.source_text)):
        text = match.group().strip()
        if not text:
            continue
        start = match.start() + len(match.group()) - len(match.group().lstrip())
        end = match.end() - len(match.group()) + len(match.group().rstrip())
        linked = [
            item.element_id
            for item in evidence.values()
            if item.source_start < end and item.source_end > start
        ]
        coverage.append(
            {
                "id": f"source_{index}",
                "text": text,
                "start": start,
                "end": end,
                "element_ids": linked,
                "status": "linked" if linked else "unlinked",
            }
        )

    # A gateway is routing, not work. Detect a stated decision action without an activity.
    for match in DECISION.finditer(ir.source_text):
        if not any(
            DECISION.search(node.name)
            and node.id in evidence
            and evidence[node.id].source_start <= match.start()
            and evidence[node.id].source_end >= match.end()
            for node in tasks
        ):
            issues.append(
                Issue(
                    code="SOURCE_ACTION_OMITTED",
                    category="model",
                    severity="error",
                    message="В описании есть принятие решения, но соответствующая задача не найдена.",
                    source_quote=match.group(),
                    suggested_action=(
                        "Добавьте действие исполнителя перед выбором маршрута. Шлюз его не заменяет."
                    ),
                )
            )

    for node in tasks:
        quote = evidence[node.id].exact_quote if node.id in evidence else ""
        positive, negative = bool(APPROVE.search(quote)), bool(REJECT.search(quote))
        # Both outcomes in one quote are ambiguous, so this narrow rule cannot decide.
        if positive == negative:
            continue
        reversed_action = (negative and APPROVE.search(node.name) and not REJECT.search(node.name)) or (
            positive and REJECT.search(node.name) and not APPROVE.search(node.name)
        )
        if reversed_action:
            issues.append(
                Issue(
                    code="SOURCE_ACTION_CONTRADICTION",
                    category="model",
                    severity="error",
                    message="Название действия противоречит решению в его цитате.",
                    element_ids=[node.id],
                    source_quote=quote,
                    suggested_action="Сохраните исходный смысл одобрения или отказа.",
                )
            )

    # Explicit data names are part of the process, not decoration in provenance.
    visible_texts = [node.name for node in ir.nodes] + [
        f"{flow.name} {flow.condition or ''}" for flow in ir.flows
    ]
    for match in NAMED_DATA.finditer(ir.source_text):
        name = match.group("angle") or match.group("straight")
        if not any(name in text for text in visible_texts):
            issues.append(Issue(
                code="SOURCE_NAMED_DATA_OMITTED", category="model", severity="error",
                message="На схеме потеряно явно указанное название файла, поля или документа.",
                source_quote=match.group(),
                element_ids=[item.element_id for item in evidence.values()
                             if item.source_start < match.end() and item.source_end > match.start()],
                suggested_action=(
                    f"Сохраните точное название «{name}» в соответствующем действии или условии."
                ),
            ))

    # Preserve both terms in an explicitly compounded branch condition.
    for match in COMPOUND_CONDITION.finditer(ir.source_text):
        first, second = _words(match.group(1)), _words(match.group(2))
        candidates = [
            flow
            for flow in ir.flows
            if flow.kind == "sequence"
            and _compatible_words(second, _words(f"{flow.name} {flow.condition or ''}"))
        ]
        missing = [flow for flow in candidates if not _condition_enforced(ir, flow, first)]
        if missing:
            issues.append(
                Issue(
                    code="SOURCE_CONDITION_INCOMPLETE",
                    category="model",
                    severity="error",
                    message="Условие ветви потеряло часть явно заданного сочетания условий.",
                    element_ids=[flow.id for flow in missing],
                    source_quote=match.group().rstrip(","),
                    suggested_action="Сохраните обе части условия; не меняйте приоритет исходов.",
                )
            )
    return {
        "version": SEMANTIC_RULES_VERSION,
        "status": "needs_review" if issues else "rules_passed",
        "human_review_required": True,
        "scope": "Привязки к тексту и ограниченные правила; полнота смысла требует проверки аналитиком.",
        "coverage": coverage,
        "issues": [issue.model_dump() for issue in issues],
        "linked_fragments": sum(row["status"] == "linked" for row in coverage),
        "total_fragments": len(coverage),
    }
