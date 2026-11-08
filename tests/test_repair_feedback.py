import copy
import json

import pytest

from backend.app.domain import PipelineError
from backend.app.llm import _check_input
from backend.app.repair_feedback import bounded_repair_feedback


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


def test_single_small_feedback_preserves_normal_shape_and_all_values():
    original = [
        {
            "python_code": 'bpmn.document(id="d")',
            "issues": [{"code": "X", "element_ids": ["a"], "message": "Ошибка"}],
            "document_context": {"declared_roles": []},
        }
    ]
    frozen = copy.deepcopy(original)
    result = bounded_repair_feedback(original)
    assert result == original == frozen
    assert "feedback_compaction" not in result[0]
    _check_input("Описание", result)


def test_only_latest_program_is_sent_and_history_has_explicit_summary():
    code = "bpmn.content(element='x',text='" + "Ж" * 30000 + "')"
    previous = {
        "python_code": code,
        "issues": [{"code": "SCHEMA_PARENT", "element_ids": ["one"], "message": "Ошибка поля"}],
    }
    latest = {
        "python_code": code.replace("'x'", "'y'"),
        "issues": [{"code": "SCHEMA_ATTRIBUTE", "element_ids": ["two"]}],
    }
    original = [previous, latest]
    frozen = copy.deepcopy(original)
    result = bounded_repair_feedback(original)
    assert result[-1]["python_code"] == latest["python_code"]
    assert result[0]["python_code"] is None
    assert result[0]["history_summary"] is True
    assert result[0]["issues"][0]["code"] == "SCHEMA_PARENT"
    assert size(result) <= 100000
    assert original == frozen
    _check_input("Описание", result)


def test_large_context_is_explicitly_dropped_while_latest_code_and_issue_ids_survive():
    code = "x" * 60000
    source = [
        {
            "python_code": code,
            "issues": [{"code": "UNASSIGNED_ROLE", "element_ids": ["task"], "message": "Кто исполняет?"}],
            "document_context": {"long": "Ж" * 80000},
        }
    ]
    frozen = copy.deepcopy(source)
    result = bounded_repair_feedback(source)
    assert result[-1]["python_code"] == code
    assert result[-1]["issues"] == source[-1]["issues"]
    assert "document_context" not in result[-1]
    assert result[-1]["feedback_compaction"]["document_context_omitted"] is True
    assert source == frozen
    _check_input("Описание", result)


def test_big_diagnostics_keep_every_current_code_and_id_with_truncation_marker():
    issues = [
        {
            "code": "UNASSIGNED_ROLE",
            "element_ids": [f"task_{i}"],
            "message": "Ошибка" * 5000,
            "source_quote": "Источник" * 2000,
            "suggested_action": "Назначьте роль" * 1000,
            "witness_path": ["x"] * 1000,
        }
        for i in range(30)
    ]
    original = [{"python_code": "x" * 60000, "issues": issues}]
    frozen = copy.deepcopy(original)
    result = bounded_repair_feedback(original)
    assert result[-1]["python_code"] == original[-1]["python_code"]
    assert [(i["code"], i["element_ids"]) for i in result[-1]["issues"]] == [
        (i["code"], i["element_ids"]) for i in issues
    ]
    assert result[-1]["feedback_compaction"]["diagnostic_text_truncated"] is True
    assert original == frozen
    _check_input("Описание", result)


def test_json_escaping_is_counted_and_oversized_latest_program_is_not_silently_cut():
    # This literal fits the raw 64KB program cap, but exceeds 100KB after JSON escaping.
    code = "bpmn.content(element='x',text='" + "\\" * 60000 + "')"
    assert len(code.encode()) < 65536
    assert size([{"python_code": code}]) > 100000
    original = [{"python_code": code, "issues": []}]
    with pytest.raises(PipelineError) as caught:
        bounded_repair_feedback(original)
    assert caught.value.issue.code == "LLM_FEEDBACK_LIMIT"
    assert original[0]["python_code"] == code


def test_history_omission_is_marked_and_not_counted_as_current_issues():
    old = {"python_code": "old", "issues": [{"code": "OLD", "element_ids": ["a"], "message": "long" * 300}]}
    latest = {"python_code": "new", "issues": [{"code": "CURRENT", "element_ids": ["b"]}]}
    result = bounded_repair_feedback([old] * 20 + [latest], max_bytes=1000)
    assert len(result) == 1
    assert result[0]["python_code"] == "new"
    assert result[0]["issues"] == latest["issues"]
    assert result[0]["feedback_compaction"]["history_context_omitted"] is True
    assert size(result) <= 1000


def test_empty_feedback_stays_empty_and_bad_json_is_controlled():
    assert bounded_repair_feedback([]) == []
    with pytest.raises(PipelineError, match="JSON"):
        bounded_repair_feedback([{"python_code": "safe", "issues": [], "bad": object()}])
