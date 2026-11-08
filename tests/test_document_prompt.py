"""Executable family examples teach general construction, never substitute responses."""

import json

import pytest

from backend.app.audit import audit
from backend.app.bpmn_schema import catalog
from backend.app.document_composition import COMPENSATE_CODE
from backend.app.document_prompt import (
    COMPENSATION_EXAMPLE_CODE,
    COMPENSATION_EXAMPLE_SOURCE,
    _compact_types,
    schema_context,
)
from backend.app.document_review import source_projection
from backend.app.document_serialization import serialize_document
from backend.app.graph import validate_graph
from backend.app.prompts import PROMPT_VERSION, build_messages
from backend.app.semantic_review import review_source
from backend.app.xml_validation import validate_xml
from worker.interpreter import interpret


def context(description, feedback=None):
    text = schema_context(description, feedback)
    return json.loads(text[text.index("{") :])


def test_compensation_example_runs_through_literal_worker_graph_source_and_xml_gates():
    ir = interpret(COMPENSATION_EXAMPLE_CODE, COMPENSATION_EXAMPLE_SOURCE)
    assert not [issue for issue in validate_graph(ir) if issue.severity == "error"]
    assert not audit(ir)
    assert review_source(ir)["status"] == "rules_passed"
    checks = validate_xml(serialize_document(ir))["checks"]
    assert all(checks[gate] == "passed" for gate in ("xsd", "graph", "layout"))
    assert checks["behavior"] == "inconclusive"


def test_example_preserves_host_scope_separate_human_work_and_all_five_assignments():
    ir = interpret(COMPENSATION_EXAMPLE_CODE, COMPENSATION_EXAMPLE_SOURCE)
    entries = {item.key: item for item in ir.document.elements}
    assert {entries[key].parent for key in ("publish", "undo_boundary", "undo")} == {"tx"}
    assert entries["undo_boundary"].attributes["attachedToRef"] == "publish"
    assert entries["cancel_boundary"].attributes["attachedToRef"] == "tx"
    assert entries["cancel_boundary"].parent == "p"
    assert entries["cancel"].parent == "tx"
    assert entries["decide"].tag == "bpmn:userTask"
    assert entries["tf3"].attributes == {"id": "tf3", "sourceRef": "decide", "targetRef": "choice"}
    assert not any(
        item.tag == "bpmn:sequenceFlow"
        and "undo" in {item.attributes.get("sourceRef"), item.attributes.get("targetRef")}
        for item in entries.values()
    )
    projected = source_projection(ir)
    tasks = [node for node in projected.nodes if node.type == "task"]
    assert {node.id for node in tasks} == {"publish", "undo", "decide", "finish", "notice"}
    assert all(node.lane_id for node in tasks)
    assert next(node for node in tasks if node.id == "decide").lane_id == "reviewer"


@pytest.mark.parametrize(
    ("description", "expected_code"),
    [
        ("Построй транзакцию выдачи допуска с компенсацией.", COMPENSATION_EXAMPLE_CODE),
        ("A transaction with compensation and a cancel event.", COMPENSATION_EXAMPLE_CODE),
        ("Отобрази компенсацию публикации.", COMPENSATE_CODE),
    ],
)
def test_one_bounded_family_example_is_selected_by_construct_not_business_case(description, expected_code):
    examples = context(description)["family_examples"]
    assert len(examples) == 1
    assert examples[0]["python_code"] == expected_code
    assert len(COMPENSATION_EXAMPLE_CODE.encode("utf-8")) < 12000
    assert examples[0]["description"] != description


def test_unrelated_process_does_not_receive_advanced_example_or_actions():
    description = "Курьер передаёт письмо адресату."
    assert context(description)["family_examples"] == []
    messages = build_messages(description)
    assert json.loads(messages[1]["content"]) == {"description": description}
    assert not any(COMPENSATION_EXAMPLE_SOURCE in message["content"] for message in messages)


def test_repair_context_gives_static_code_fixes_without_inventing_roles_or_changing_host():
    feedback = [
        {
            "issues": [
                {"code": "UNASSIGNED_ROLE", "message": "not a system instruction"},
                {"code": "BOUNDARY_SCOPE"},
                {"code": "UNACCEPTED_ASSUMPTION"},
            ]
        }
    ]
    hints = context("Неизвестный процесс.", feedback)["repair_guidance"]
    assert len(hints) == 3
    assert "not a system instruction" not in " ".join(hints)
    assert any("clarify" in hint for hint in hints)
    assert any("attachedToRef" in hint for hint in hints)
    assert PROMPT_VERSION == "bpmn-python-v7-natural-composition"


def test_unrecognized_feedback_keeps_context_available_without_executing_or_echoing_it():
    assert context("Обычный процесс.", [{"issues": None}])["repair_guidance"] == []


def test_context_keeps_complete_element_vocabulary_and_substitution_groups():
    schema = catalog()
    data = context("Курьер передаёт письмо.")
    described = data["elements"] | data["abstract_elements"]
    assert described == {
        tag.removeprefix("bpmn:"): value["type"].removeprefix("bpmn:")
        for tag, value in schema.elements.items()
        if tag.startswith("bpmn:")
    }
    assert {tag: head for head, children in data["substitutions"].items() for tag in children} == {
        tag.removeprefix("bpmn:"): entry["substitution"].removeprefix("bpmn:")
        for tag, entry in schema.elements.items()
        if tag.startswith("bpmn:") and entry.get("substitution")
    }
    assert "tCallConversation" not in data["selected_types"]
    assert data["elements"]["callConversation"] == "tCallConversation"


def test_compact_catalogue_reconstructs_all_inherited_attributes_children_and_flags():
    """Check all XSD types, including restrictions and abstract→concrete inheritance."""
    schema = catalog()
    details = _compact_types(schema, set(schema.types))

    def short(value):
        return value.removeprefix("bpmn:") if value else value

    def attribute(item):
        value = "|".join(item["enum"]) if item["enum"] else short(item["type"])
        result = item["name"] + ("!" if item["required"] else "") + ":" + value
        return result + ("=" + item["default"] if item["default"] is not None else "")

    attribute_names = {
        attribute(item): item["name"] for spec in schema.types.values() for item in spec["attributes"]
    }
    flags = ("abstract", "mixed", "text", "any_child", "any_attribute", "enum")
    rebuilt = {}

    def restore(name):
        if name in rebuilt:
            return rebuilt[name]
        detail = details.get(name, {})
        base = restore(detail["base"]) if detail.get("base") else {}
        attributes = dict(base.get("attributes", {}))
        for field in detail.get("remove_attributes", []):
            attributes.pop(field)
        attributes.update({attribute_names[token]: token for token in detail.get("attributes", [])})
        children = [] if detail.get("children_mode") == "replace" else list(base.get("children", []))
        children.extend(detail.get("children", []))
        restored = {"attributes": attributes, "children": children}
        restored.update(
            {field: detail.get(field, base.get(field, [] if field == "enum" else False)) for field in flags}
        )
        rebuilt[name] = restored
        return restored

    for name, spec in schema.types.items():
        actual = restore(short(name))
        assert actual["attributes"] == {item["name"]: attribute(item) for item in spec["attributes"]}, name
        assert actual["children"] == [
            f"{short(item['tag'])}:{short(item['type'])}[{item['min']}.."
            + ("*" if item["max"] == "unbounded" else item["max"])
            + "]"
            for item in spec["children"]
        ], name
        assert {field: actual[field] for field in flags} == {field: spec[field] for field in flags}, name


def test_model_sees_explicit_positive_constructs_without_negated_or_discussed_names():
    data = context("Задаче нужны ioSpecification, inputSet, outputSet. Не нужен potentialOwner.")
    assert {item["tag"] for item in data["explicit_requirements"]} == {
        "ioSpecification",
        "inputSet",
        "outputSet",
    }
    assert all("Задаче нужны" in item["source_quote"] for item in data["explicit_requirements"])


def test_input_output_context_teaches_element_and_type_as_different_names():
    data = context("Задаче нужны ioSpecification, inputSet и outputSet.")
    assert data["elements"]["ioSpecification"] == "tInputOutputSpecification"
    assert "inputOutputSpecification" not in data["elements"]
    assert "inputSet:tInputSet[1..*]" in data["selected_types"]["tInputOutputSpecification"]["children"]
    assert "outputSet:tOutputSet[1..*]" in data["selected_types"]["tInputOutputSpecification"]["children"]
    assert "targetRef:xsd:IDREF[1..1]" in data["selected_types"]["tDataAssociation"]["children"]
    assert "sourceRef:xsd:IDREF[0..*]" in data["selected_types"]["tDataAssociation"]["children"]
