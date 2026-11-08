"""Application pipeline tests with an explicit trusted fixture runner (not live)."""

import json

from backend.app.bpmn_schema import NAMESPACES
from backend.app.revision_review import review_revision
from tests.test_service import SequenceProvider, service

SOURCE = "Менеджер проверяет заявку."
CODE = """bpmn.document(id="d", name="Проверка")
bpmn.element(id="p",tag="process",parent="d")
bpmn.element(id="roles",tag="laneSet",parent="p")
bpmn.element(id="manager",tag="lane",parent="roles",name="Менеджер")
bpmn.element(id="assignment",tag="flowNodeRef",parent="manager",text="sub")
bpmn.element(id="s",tag="startEvent",parent="p")
bpmn.element(id="sub",tag="subProcess",parent="p",name="Проверка заявки")
bpmn.element(id="ss",tag="startEvent",parent="sub")
bpmn.element(id="task",tag="userTask",parent="sub",name="Проверить заявку",
             source_quote="Менеджер проверяет заявку")
bpmn.element(id="ee",tag="endEvent",parent="sub")
bpmn.element(id="sf1",tag="sequenceFlow",parent="sub",sourceRef="ss",targetRef="task")
bpmn.element(id="sf2",tag="sequenceFlow",parent="sub",sourceRef="task",targetRef="ee")
bpmn.element(id="e",tag="endEvent",parent="p")
bpmn.element(id="f1",tag="sequenceFlow",parent="p",sourceRef="s",targetRef="sub")
bpmn.element(id="f2",tag="sequenceFlow",parent="p",sourceRef="sub",targetRef="e")"""


async def test_document_pipeline_persists_original_code_complete_ir_and_xml(tmp_path):
    result = await service(tmp_path, SequenceProvider([CODE])).generate(SOURCE)
    assert result["status"] == "ready", result["issues"]
    assert result["python_code"] == CODE
    assert result["ir"]["schema_version"] == "2.0"
    assert len(result["ir"]["document"]["elements"]) == 15
    assert all(result["checks"][gate] == "passed" for gate in ("code", "graph", "xsd", "layout"))
    assert result["checks"]["behavior"] == "inconclusive"  # Nested completion is not legacy token semantics.
    assert result["checks"]["import"] == "not_run"
    assert result["semantic_review"]["linked_fragments"] == 1
    metadata = json.loads((tmp_path / result["id"] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["compiler_profile"] == "bpmn-2.0.2-document"
    assert len(metadata["schema_context_sha256"]) == 64
    assert (tmp_path / result["id"] / "generated.py").read_text(encoding="utf-8") == CODE


async def test_complete_document_graph_error_can_be_repaired_without_losing_nesting(tmp_path):
    bad = CODE.replace('sourceRef="ss"', 'sourceRef="missing"')
    provider = SequenceProvider([bad, CODE])
    result = await service(tmp_path, provider).generate(SOURCE)
    assert result["status"] == "ready", result["issues"]
    assert result["attempt_count"] == 2
    assert provider.calls[1][0]["issues"][0]["code"] == "UNKNOWN_REFERENCE"
    assert 'tag="subProcess"' in result["python_code"]


async def test_repair_receives_all_graph_failures_in_one_attempt(tmp_path):
    bad = CODE.replace('sourceRef="ss"', 'sourceRef="missing_source"').replace(
        'targetRef="ee"', 'targetRef="missing_target"'
    )
    provider = SequenceProvider([bad, CODE])
    result = await service(tmp_path, provider).generate(SOURCE)
    assert result["status"] == "ready", result["issues"]
    issues = provider.calls[1][0]["issues"]
    invalid_flows = {element_id for issue in issues for element_id in issue["element_ids"]}
    assert {"sf1", "sf2"} <= invalid_flows
    assert result["attempt_count"] == 2


async def test_missing_model_citation_is_repaired_before_questioning_analyst(tmp_path):
    bad = CODE.replace(',\n             source_quote="Менеджер проверяет заявку"', '')
    provider = SequenceProvider([bad, CODE])
    result = await service(tmp_path, provider).generate(SOURCE)
    assert result["status"] == "ready", result["issues"]
    assert result["attempt_count"] == 2
    assert provider.calls[1][0]["issues"][0]["code"] == "ACTION_WITHOUT_SOURCE"


async def test_missing_model_assignment_is_repaired_without_inventing_a_role(tmp_path):
    bad = "\n".join(line for line in CODE.splitlines() if not any(
        marker in line for marker in ('id="roles"', 'id="manager"', 'id="assignment"')
    ))
    provider = SequenceProvider([bad, CODE])
    result = await service(tmp_path, provider).generate(SOURCE)
    assert result["status"] == "ready", result["issues"]
    assert result["attempt_count"] == 2
    assert provider.calls[1][0]["issues"][0]["code"] == "UNASSIGNED_ROLE"
    assert result["python_code"] == CODE


async def test_document_revision_compares_nested_names_and_preserves_manual_geometry(tmp_path):
    from lxml import etree

    result = await service(tmp_path, SequenceProvider([CODE])).generate(SOURCE)
    assert result["status"] == "ready", result["issues"]
    root = etree.fromstring(result["bpmn_xml"].encode())
    root.find('.//bpmn:userTask[@id="task"]', NAMESPACES).set("name", "Проверить другую заявку")
    root.find('.//bpmndi:BPMNShape[@bpmnElement="task"]/dc:Bounds', NAMESPACES).set("x", "300")
    edited = etree.tostring(root, encoding="unicode")
    report = review_revision(edited, result)
    assert report["checks"]["xsd"] == report["checks"]["graph"] == "passed"
    assert report["semantic_diff"]["status"] == "changed"
    assert any(change["element_id"] == "task" for change in report["semantic_diff"]["changes"])
    assert etree.tostring(root, encoding="unicode") == edited


async def test_all_document_examples_are_usable_in_explicit_mock_mode(tmp_path):
    from backend.app.examples import load_examples
    from backend.app.llm import MockProvider

    cases = [case for case in load_examples()["cases"] if case["id"].startswith("F")]
    assert len(cases) == 6
    for case in cases:
        result = await service(tmp_path / case["id"], MockProvider()).generate(case["text"])
        assert result["status"] == "ready", (case["id"], result["issues"])
        assert result["mode"] == "mock" and "fixture" in result["summary"]
        assert result["ir"]["document"]


async def test_standard_definition_library_does_not_require_fabricated_di(tmp_path):
    code = """bpmn.document(id="d",name="Библиотека сообщений")
bpmn.element(id="m",tag="message",parent="d",name="Заказ")
bpmn.element(id="r",tag="resource",parent="d",name="Менеджер")"""
    result = await service(tmp_path, SequenceProvider([code])).generate(
        "Сообщение «Заказ» и ресурс «Менеджер»."
    )
    assert result["status"] == "ready", result["issues"]
    assert result["checks"]["xsd"] == result["checks"]["graph"] == "passed"
    assert result["checks"]["layout"] == "not_run"
    assert "BPMNDiagram" not in result["bpmn_xml"]
    assert result["document_kind"] == "definitions"
