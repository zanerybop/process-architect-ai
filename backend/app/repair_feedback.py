"""Bound repair prompts without changing model programs or saved attempt artifacts."""

import json

from backend.app.domain import PipelineError

MAX_REPAIR_FEEDBACK_BYTES = 100_000


def _size(value):
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise PipelineError("LLM_FEEDBACK_LIMIT", "Контекст исправления не сериализуется в JSON.") from error


def _identity(issue):
    return {"code": issue.get("code"), "element_ids": list(issue.get("element_ids") or [])}


def _excerpt(value, limit):
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[:limit] + "…"


def bounded_repair_feedback(feedback: list[dict], *, max_bytes=MAX_REPAIR_FEEDBACK_BYTES) -> list[dict]:
    """Keep the complete latest Python and current issue identities within 100 KB.

    Previous programs stay in metadata, not the next model request. Optional
    context and diagnostic prose may be shortened with explicit markers. If
    even the unchanged latest code plus issue codes/IDs cannot fit, fail closed;
    silently truncating Python would produce a different program.
    """
    if not isinstance(max_bytes, int) or not 512 <= max_bytes <= MAX_REPAIR_FEEDBACK_BYTES:
        raise ValueError("Repair feedback budget must be 512..100000 bytes")
    if not isinstance(feedback, list) or not all(isinstance(item, dict) for item in feedback):
        raise PipelineError("LLM_FEEDBACK_LIMIT", "Ожидается список диагностик исправления.")
    if not feedback:
        return []
    _size(feedback)  # Validate JSON before any summarization can hide malformed data.
    latest = dict(feedback[-1])
    if not isinstance(latest.get("python_code"), (str, type(None))):
        raise PipelineError("LLM_FEEDBACK_LIMIT", "Последняя программа должна быть строкой или null.")
    current_issues = latest.get("issues", [])
    if not isinstance(current_issues, list) or not all(isinstance(issue, dict) for issue in current_issues):
        raise PipelineError("LLM_FEEDBACK_LIMIT", "Список текущих ошибок имеет недопустимый формат.")
    marks = dict(latest.get("feedback_compaction") or {})
    history = None
    if len(feedback) > 1:
        previous = feedback[:-1]
        old_issues = [issue for item in previous for issue in item.get("issues", [])]
        if not all(isinstance(issue, dict) for issue in old_issues):
            raise PipelineError("LLM_FEEDBACK_LIMIT", "История ошибок имеет недопустимый формат.")
        history = {
            "history_summary": True,
            "attempt_count": len(previous),
            "issues": [
                {**_identity(issue), "message": _excerpt(issue.get("message"), 160)}
                for issue in old_issues[-30:]
            ],
            "omitted_issue_count": max(0, len(old_issues) - 30),
            "python_code": None,
        }
        marks["previous_python_code_omitted"] = len(previous)

    def result():
        value = dict(latest)
        if marks:
            value["feedback_compaction"] = dict(marks)
        return ([history] if history is not None else []) + [value]

    if _size(result()) <= max_bytes:
        return result()
    if history is not None:
        # Current diagnostics take precedence over old failures and their IDs.
        marks["history_context_omitted"] = True
        history = None
        if _size(result()) <= max_bytes:
            return result()
    if "document_context" in latest:
        latest.pop("document_context")
        marks["document_context_omitted"] = True
        if _size(result()) <= max_bytes:
            return result()
    for limit in (1000, 400, 120, 0):
        clipped = []
        for issue in current_issues:
            entry = _identity(issue)
            for field in ("category", "severity"):
                if field in issue:
                    entry[field] = issue[field]
            if limit:
                for field in ("message", "source_quote", "suggested_action"):
                    if field in issue:
                        entry[field] = _excerpt(issue[field], limit)
            clipped.append(entry)
        latest["issues"] = clipped
        marks["diagnostic_text_truncated"] = True
        marks["diagnostic_text_limit_characters"] = limit
        marks["witness_paths_omitted"] = any(issue.get("witness_path") for issue in current_issues)
        if _size(result()) <= max_bytes:
            return result()
    # Remove optional top-level extras, keeping the full last code and every
    # current issue identity. The dropped information remains in saved metadata.
    latest = {"python_code": latest.get("python_code"), "issues": [_identity(i) for i in current_issues]}
    marks["optional_fields_omitted"] = True
    if _size(result()) <= max_bytes:
        return result()
    raise PipelineError(
        "LLM_FEEDBACK_LIMIT",
        "Последняя программа и коды/ID текущих ошибок не помещаются в лимит JSON-контекста исправления. "
        "Программа не усекалась; исходные попытки сохранены отдельно.",
    )
