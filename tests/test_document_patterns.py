"""Executable generic family context; no live provider and no benchmark rewrites."""

import json

import pytest
from lxml import etree

from backend.app.audit import audit
from backend.app.bpmn_schema import NAMESPACES
from backend.app.document_patterns import (
    CONVERSATION_CODE,
    CONVERSATION_SOURCE,
    DATA_CODE,
    DATA_SOURCE,
    NESTED_CODE,
    NESTED_SOURCE,
    PATTERNS,
    select_patterns,
)
from backend.app.document_prompt import schema_context
from backend.app.document_review import source_projection
from backend.app.document_serialization import serialize_document
from backend.app.graph import validate_graph
from backend.app.semantic_review import review_source
from backend.app.xml_validation import validate_xml
from tests.test_service import SequenceProvider, service
from worker.interpreter import interpret


@pytest.mark.parametrize("pattern", PATTERNS, ids=lambda pattern: pattern["id"])
def test_family_program_is_valid_literal_python_ir_xml_and_source(pattern):
    ir = interpret(pattern["python_code"], pattern["description"])
    assert ir.document and ir.schema_version == "2.0"
    assert not ir.assumptions
    assert not [issue for issue in validate_graph(ir) if issue.severity == "error"]
    assert not audit(ir)
    assert review_source(ir)["status"] == "rules_passed"
    validation = validate_xml(serialize_document(ir))
    assert all(validation["checks"][gate] == "passed" for gate in ("graph", "xsd", "layout"))
    assert validation["checks"]["behavior"] == "inconclusive"
    assert validation["checks"]["import"] == "not_run"


@pytest.mark.parametrize("pattern", PATTERNS, ids=lambda pattern: pattern["id"])
async def test_family_program_reaches_ready_through_real_generation_service_with_explicit_fixture(
    tmp_path, pattern
):
    result = await service(tmp_path, SequenceProvider([pattern["python_code"]])).generate(
        pattern["description"]
    )
    assert result["status"] == "ready", result["issues"]
    assert result["attempt_count"] == 1
    assert result["python_code"] == pattern["python_code"]


def tree(code, source):
    return etree.fromstring(serialize_document(interpret(code, source)).encode())


def test_data_example_keeps_typed_io_sets_associations_state_store_and_resource_assignment():
    root = tree(DATA_CODE, DATA_SOURCE)
    task = root.find('.//bpmn:userTask[@id="process_file"]', NAMESPACES)
    io = task.find("bpmn:ioSpecification", NAMESPACES)
    assert io is not None
    assert io.find("bpmn:inputSet/bpmn:dataInputRefs", NAMESPACES).text == "input"
    assert io.find("bpmn:outputSet/bpmn:dataOutputRefs", NAMESPACES).text == "output"
    for name in ("dataInput", "dataOutput"):
        assert io.find(f"bpmn:{name}", NAMESPACES).get("itemSubjectRef") == "string_type"
    assert root.find('bpmn:itemDefinition[@id="string_type"]', NAMESPACES).get("structureRef") == "xsd:string"
    assert root.find('bpmn:dataStore[@id="catalog"]', NAMESPACES) is not None
    assert root.find('.//bpmn:dataState[@id="state"]', NAMESPACES).get("name") == "Исходный"
    for name, source, target in (
        ("dataInputAssociation", "file", "input"),
        ("dataOutputAssociation", "output", "store"),
    ):
        association = task.find(f"bpmn:{name}", NAMESPACES)
        assert "sourceRef" not in association.attrib and "targetRef" not in association.attrib
        assert association.find("bpmn:sourceRef", NAMESPACES).text == source
        assert association.find("bpmn:targetRef", NAMESPACES).text == target
    assert task.find("bpmn:potentialOwner/bpmn:resourceRef", NAMESPACES).text == "technician"
    assert root.find(".//bpmn:textAnnotation/bpmn:text", NAMESPACES).text == "Исходные измерения сохраняются"


def test_nested_example_assigns_real_service_and_keeps_event_flow_inside_its_scope():
    ir = interpret(NESTED_CODE, NESTED_SOURCE)
    root = etree.fromstring(serialize_document(ir).encode())
    event = root.find('.//bpmn:subProcess[@id="urgent"]', NAMESPACES)
    assert event.get("triggeredByEvent") == "true"
    start = event.find("bpmn:startEvent", NAMESPACES)
    assert start.get("isInterrupting") == "false"
    assert start.find("bpmn:messageEventDefinition", NAMESPACES).get("messageRef") == "drought"
    projected = source_projection(ir)
    roles = {lane.id: lane.name for lane in projected.lanes}
    assigned = {node.id: roles[node.lane_id] for node in projected.nodes if node.type == "task"}
    assert assigned == {"check": "Агроном", "log": "Агроном", "water": "Агроном", "register": "Сервис"}
    assert root.find('.//bpmn:lane[@id="inner_agronomist"]/..', NAMESPACES).getparent().get("id") == "prepare"
    assert len(event.findall("bpmn:sequenceFlow", NAMESPACES)) == 2
    assert not root.xpath(
        ".//bpmn:sequenceFlow[@sourceRef='urgent' or @targetRef='urgent']", namespaces=NAMESPACES
    )
    mi = root.find(".//bpmn:multiInstanceLoopCharacteristics", NAMESPACES)
    assert mi.get("isSequential") == "false"
    assert mi.find("bpmn:loopCardinality", NAMESPACES).text == "4"
    assert mi.find("bpmn:completionCondition", NAMESPACES) is None
    ad_hoc = root.find(".//bpmn:adHocSubProcess", NAMESPACES)
    assert ad_hoc.get("ordering") == "Parallel"
    assert ad_hoc.get("cancelRemainingInstances") == "false"
    assert ad_hoc.find("bpmn:completionCondition", NAMESPACES).text == "Журнал заполнен и полив настроен"
    assert not ad_hoc.findall("bpmn:sequenceFlow", NAMESPACES)


def test_conversation_example_preserves_topics_links_and_call_to_global_role_mapping():
    root = tree(CONVERSATION_CODE, CONVERSATION_SOURCE)
    assert not root.xpath(".//bpmn:process|.//bpmn:task|.//bpmn:sequenceFlow", namespaces=NAMESPACES)
    global_roles = root.findall("bpmn:globalConversation/bpmn:participant", NAMESPACES)
    local_roles = root.findall("bpmn:collaboration/bpmn:participant", NAMESPACES)
    assert (
        {role.get("name") for role in global_roles}
        == {role.get("name") for role in local_roles}
        == {"Издатель", "Типография"}
    )
    assert {role.get("id") for role in global_roles}.isdisjoint(role.get("id") for role in local_roles)
    call = root.find(".//bpmn:callConversation", NAMESPACES)
    assert call.get("calledCollaborationRef") == "template"
    mappings = {
        (
            entry.find("bpmn:innerParticipantRef", NAMESPACES).text,
            entry.find("bpmn:outerParticipantRef", NAMESPACES).text,
        )
        for entry in call.findall("bpmn:participantAssociation", NAMESPACES)
    }
    assert mappings == {("inner_publisher", "publisher"), ("inner_printer", "printer")}
    conversations = root.findall(".//bpmn:subConversation/bpmn:conversation", NAMESPACES)
    assert {entry.get("name") for entry in conversations} == {"Макет", "Бумага"}
    assert all(
        {child.text for child in entry.findall("bpmn:participantRef", NAMESPACES)} == {"publisher", "printer"}
        for entry in conversations
    )
    assert {entry.get("sourceRef") for entry in root.findall(".//bpmn:conversationLink", NAMESPACES)} == {
        "publisher",
        "printer",
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ioSpecification c inputSet и outputSet", {"data_io_resources"}),
        ("Объект данных получает potentialOwner", {"data_io_resources"}),
        ("Событийный подпроцесс", {"nested_roles_events_loops"}),
        ("Параллельная многоэкземплярная задача", {"nested_roles_events_loops"}),
        ("callConversation использует globalConversation", {"reusable_conversations"}),
        ("Разговор участников", {"reusable_conversations"}),
        ("Курьер передаёт письмо.", set()),
    ],
)
def test_patterns_are_selected_by_bpmn_construct_not_domain_or_case_number(text, expected):
    assert {pattern["id"] for pattern in select_patterns(text)} == expected


def test_schema_failure_keeps_io_repair_guidance_even_when_business_source_is_short():
    text = schema_context("Проверка измерений.", [{"issues": [{"code": "SCHEMA_PARENT"}]}])
    context = json.loads(text[text.index("{") :])
    assert any("сохраняй все явно требуемые IO" in hint for hint in context["repair_guidance"])
