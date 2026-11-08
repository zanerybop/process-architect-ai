"""Full document API stays literal-only, schema-driven and reference-preserving."""

import json

import pytest
from lxml import etree

from backend.app.bpmn_schema import NAMESPACES, SCHEMA_DIRECTORY, catalog
from backend.app.builder import Builder
from backend.app.document_prompt import schema_context
from backend.app.domain import PipelineError, ProcessIR
from worker.interpreter import interpret


def test_catalogue_covers_every_official_semantic_declaration():
    root = etree.parse(str(SCHEMA_DIRECTORY / "Semantic.xsd"))
    elements = root.findall("{http://www.w3.org/2001/XMLSchema}element")
    types = root.findall("{http://www.w3.org/2001/XMLSchema}complexType")
    schema = catalog()
    assert len(elements) == 133
    assert all("bpmn:" + item.get("name") in schema.elements for item in elements)
    assert all("bpmn:" + item.get("name") in schema.types for item in types)
    assert schema.derived_from("tTransaction", "tActivity")
    assert schema.derived_from("tChoreographyTask", "tFlowNode")
    assert schema.element_type("timeDuration", "tTimerEventDefinition") == "bpmn:tExpression"
    assert schema.element_type("boundaryEvent", "tProcess") == "bpmn:tBoundaryEvent"
    assert schema.element_type("flowNodeRef", "tLane") == "xsd:IDREF"
    assert schema.element_type("startEvent", "tTask") is None
    assert {item["tag"] for item in schema.children("tProcess")} >= {
        "bpmn:transaction",
        "bpmn:adHocSubProcess",
        "bpmn:inclusiveGateway",
        "bpmn:dataObjectReference",
    }


def test_restricted_python_builds_nested_event_document_and_roundtrips_ir():
    source = "Менеджер проверяет договор."
    ir = interpret(
        """bpmn.document(id="d", name="Договор")
bpmn.element(id="p", tag="process", parent="d")
bpmn.element(id="sub", tag="subProcess", parent="p", name="Подготовка")
bpmn.element(id="task", tag="userTask", parent="sub", name="Проверить договор",
             source_quote="Менеджер проверяет договор")
bpmn.element(id="timeout", tag="boundaryEvent", parent="p", name="2 часа")
bpmn.attribute(element="timeout", name="attachedToRef", value="sub")
bpmn.attribute(element="timeout", name="cancelActivity", value=False)
bpmn.element(id="timer", tag="timerEventDefinition", parent="timeout")
bpmn.element(id="duration", tag="timeDuration", parent="timer", type="tFormalExpression", text="PT2H")""",
        source,
    )
    assert ir.schema_version == "2.0"
    assert not ir.nodes  # No flat shadow IR silently truncating nesting.
    nodes = {item.key: item for item in ir.document.elements}
    assert nodes["task"].parent == "sub"
    assert nodes["timeout"].attributes["cancelActivity"] == "false"
    assert nodes["duration"].attributes["xsi:type"] == "bpmn:tFormalExpression"
    assert ir.provenance[0].source_start == 0
    assert ProcessIR.model_validate_json(ir.model_dump_json()) == ir


@pytest.mark.parametrize(
    "tag,parent_tag",
    [
        ("scriptTask", "process"),
        ("businessRuleTask", "process"),
        ("manualTask", "process"),
        ("subProcess", "process"),
        ("transaction", "process"),
        ("adHocSubProcess", "process"),
        ("boundaryEvent", "process"),
        ("intermediateCatchEvent", "process"),
        ("intermediateThrowEvent", "process"),
        ("complexGateway", "process"),
        ("inclusiveGateway", "process"),
        ("eventBasedGateway", "process"),
        ("choreographyTask", "choreography"),
        ("callChoreography", "choreography"),
        ("subChoreography", "choreography"),
        ("conversation", "collaboration"),
        ("callConversation", "collaboration"),
        ("subConversation", "collaboration"),
        ("dataObjectReference", "process"),
        ("dataStoreReference", "process"),
    ],
)
def test_no_notation_subset_allowlist(tag, parent_tag):
    builder = Builder()
    builder.methods["document"](id="d")
    builder.methods["element"](id="p", tag=parent_tag, parent="d")
    builder.methods["element"](id="item", tag=tag, parent="p")
    assert builder.build().document.elements[-1].tag == "bpmn:" + tag


@pytest.mark.parametrize(
    "tail,code",
    [
        ('bpmn.element(id="bad", tag="task", parent="d")', "SCHEMA_PARENT"),
        ('bpmn.element(id="bad", tag="notBpmn", parent="d")', "SCHEMA_PARENT"),
        ('bpmn.element(id="p", tag="process", parent="d")', "DUPLICATE_ID"),
        ('bpmn.attribute(element="p", name="madeUp", value="x")', "SCHEMA_ATTRIBUTE"),
        ('bpmn.attribute(element="p", name="id", value="other")', "INVALID_ID"),
        (
            'bpmn.attribute(element="p", name="xsi:schemaLocation", value="file:///private")',
            "EXTERNAL_XML_REFERENCE",
        ),
        ('bpmn.content(element="missing", text="x")', "UNKNOWN_ELEMENT"),
        ('bpmn.element(id="expr", tag="flowElement", parent="p")', "SCHEMA_TYPE"),
        ('bpmn.process(id="other", name="Mix")', "DOCUMENT_MODE"),
        ('bpmn.attribute(element="p", name="name", value=open("secret"))', "CODE_POLICY"),
    ],
)
def test_invalid_full_document_calls_are_controlled(tail, code):
    with pytest.raises(PipelineError) as caught:
        interpret('bpmn.document(id="d")\nbpmn.element(id="p",tag="process",parent="d")\n' + tail)
    assert caught.value.issue.code == code


def test_inert_extension_and_mixed_content_are_not_executed_or_lost():
    ir = interpret("""bpmn.document(id="d")
bpmn.namespace(prefix="vendor", uri="urn:vendor")
bpmn.element(id="p",tag="process",parent="d")
bpmn.element(id="ext",tag="extensionElements",parent="p")
bpmn.element(id="meta",tag="vendor:metadata",parent="ext",text="before")
bpmn.element(id="nested",tag="vendor:item",parent="meta")
bpmn.attribute(element="nested",name="value",value="open('secret')")
bpmn.content(element="nested",text="",tail="after")""")
    nested = ir.document.elements[-1]
    assert nested.tail == "after"
    assert nested.attributes["value"] == "open('secret')"
    assert NAMESPACES["bpmn"] == ir.document.namespaces["bpmn"]


def test_context_includes_full_vocabulary_and_specific_schema_facts():
    text = schema_context("Транзакция с компенсацией, хореография и разговор")
    data = json.loads(text[text.index("{") :])
    assert "transaction" in data["elements"]
    assert "tTransaction" in data["selected_types"]
    assert "tChoreographyTask" in data["selected_types"]
    assert "tCompensateEventDefinition" in data["selected_types"]
    assert "tConversation" in data["selected_types"]


def test_inline_schema_attributes_are_equivalent_and_unknown_attributes_fail():
    ir = interpret('''bpmn.document(id="d")
bpmn.element(id="p",tag="process",parent="d",isExecutable=False)
bpmn.element(id="s",tag="subProcess",parent="p")
bpmn.element(id="b",tag="boundaryEvent",parent="p",attachedToRef="s",cancelActivity="True")''')
    assert ir.document.elements[-1].attributes == {"id": "b", "attachedToRef": "s", "cancelActivity": "true"}
    with pytest.raises(PipelineError) as caught:
        interpret('bpmn.document(id="d")\nbpmn.element(id="p",tag="process",parent="d",madeUp="bad")')
    assert caught.value.issue.code == "SCHEMA_ATTRIBUTE"


def test_anonymous_di_extension_type_is_representable_without_wildcard_leak():
    from backend.app.document_serialization import build_document_tree
    from backend.app.xml_validation import _schema

    ir = interpret('''bpmn.document(id="d")
bpmn.element(id="p",tag="process",parent="d")
bpmn.element(id="diagram",tag="bpmndi:BPMNDiagram",parent="d")
bpmn.element(id="plane",tag="bpmndi:BPMNPlane",parent="diagram",bpmnElement="p")
bpmn.element(id="extension",tag="di:extension",parent="plane")''')
    root = build_document_tree(ir)
    assert _schema().validate(root), _schema().error_log
    assert not catalog().types["di:DiagramElement"]["any_child"]
    assert catalog().types[catalog().element_type("di:extension", "bpmndi:BPMNPlane")]["any_child"]
