"""Generation supplies visible condition labels without rewriting imported semantics."""

import pytest
from lxml import etree

from backend.app.bpmn_schema import NAMESPACES
from backend.app.builder import Builder
from backend.app.document_serialization import arrange_document, build_document_tree
from backend.app.serialization import serialize
from backend.app.xml_validation import validate_xml
from worker.interpreter import interpret

BASE = '''bpmn.document(id="d")
bpmn.element(id="p",tag="process",parent="d")
bpmn.element(id="s",tag="startEvent",parent="p")
bpmn.element(id="g",tag="exclusiveGateway",parent="p",default="no")
bpmn.element(id="yes_end",tag="endEvent",parent="p")
bpmn.element(id="no_end",tag="endEvent",parent="p")
bpmn.element(id="first",tag="sequenceFlow",parent="p",sourceRef="s",targetRef="g")
bpmn.element(id="no",tag="sequenceFlow",parent="p",sourceRef="g",targetRef="no_end")
'''


@pytest.mark.parametrize("flow_tag,extra", [("sequenceFlow", ""), ("flowElement", ',type="tSequenceFlow"')])
@pytest.mark.parametrize("name", [None, "", "  "])
def test_condition_is_visible_in_generated_ir_and_standard_bpmn_xml(flow_tag, extra, name):
    condition = "  Сумма < 5000 & заявка согласована  "
    call = (
        f'bpmn.element(id="yes",tag="{flow_tag}",parent="p",sourceRef="g",'
        f'targetRef="yes_end"{extra}' + (f",name={name!r}" if name is not None else "") + ")\n"
    )
    ir = interpret(BASE + call + (
        'bpmn.element(id="condition",tag="conditionExpression",parent="yes",'
        f'type="tFormalExpression",text={condition!r})'
    ))
    entries = {entry.key: entry for entry in ir.document.elements}
    assert entries["yes"].attributes["name"] == condition
    assert entries["condition"].text == condition
    assert "name" not in entries["no"].attributes  # Do not invent a default branch outcome.
    xml = serialize(ir)
    root = etree.fromstring(xml.encode())
    flow = root.xpath("//*[@id='yes']")[0]
    assert flow.get("name") == condition
    assert flow.find("bpmn:conditionExpression", NAMESPACES).text == condition
    assert validate_xml(xml)["checks"]["xsd"] == "passed"


def test_explicit_label_wins_and_relayout_does_not_name_imported_flows():
    ir = interpret(BASE + '''bpmn.element(id="yes",tag="sequenceFlow",parent="p",
sourceRef="g",targetRef="yes_end",name="Одобрено")
bpmn.element(id="condition",tag="conditionExpression",parent="yes",
type="tFormalExpression",text="approval == true")''')
    root = build_document_tree(ir)
    assert root.xpath("//*[@id='yes']")[0].get("name") == "Одобрено"
    del root.xpath("//*[@id='yes']")[0].attrib["name"]
    original = etree.tostring(root)
    arranged = arrange_document(root, preserve_existing=False)
    assert arranged.xpath("//*[@id='yes']")[0].get("name") is None
    assert etree.tostring(root) == original


def test_labels_belong_to_build_snapshot_and_follow_updated_expression():
    builder = Builder()
    methods = builder.methods
    methods["document"](id="d")
    methods["element"](id="p", tag="process", parent="d")
    methods["element"](id="flow", tag="sequenceFlow", parent="p")
    methods["element"](id="condition", tag="conditionExpression", parent="flow",
                       type="tFormalExpression", text="Первое условие")
    first = builder.build()
    methods["content"](element="condition", text="Другое условие")
    second = builder.build()
    assert next(e for e in first.document.elements if e.key == "flow").attributes["name"] == "Первое условие"
    assert next(e for e in second.document.elements if e.key == "flow").attributes["name"] == "Другое условие"


def test_empty_expressions_and_foreign_metadata_are_not_used_as_labels():
    ir = interpret(BASE + '''bpmn.element(id="yes",tag="sequenceFlow",parent="p",
sourceRef="g",targetRef="yes_end")
bpmn.element(id="condition",tag="conditionExpression",parent="yes",type="tFormalExpression",text=" ")
bpmn.namespace(prefix="v",uri="urn:vendor")
bpmn.element(id="ext",tag="extensionElements",parent="p")
bpmn.element(id="fake",tag="v:sequenceFlow",parent="ext")
bpmn.element(id="fake_cond",tag="v:conditionExpression",parent="fake",text="not a BPMN condition")''')
    entries = {entry.key: entry for entry in ir.document.elements}
    assert "name" not in entries["yes"].attributes
    assert "name" not in entries["fake"].attributes


def test_mixed_content_condition_is_preserved_without_a_partial_auto_label():
    ir = interpret(BASE + '''bpmn.element(id="yes",tag="sequenceFlow",parent="p",
sourceRef="g",targetRef="yes_end")
bpmn.element(id="condition",tag="conditionExpression",parent="yes",
type="tFormalExpression",text="amount < 5")
bpmn.element(id="doc",tag="documentation",parent="condition",text="Metadata")
bpmn.content(element="doc",text="Metadata",tail=" and approved")''')
    entries = {entry.key: entry for entry in ir.document.elements}
    assert "name" not in entries["yes"].attributes
    xml = serialize(ir)
    assert validate_xml(xml)["checks"]["xsd"] == "passed"
    root = etree.fromstring(xml.encode())
    condition = root.find(".//bpmn:conditionExpression", NAMESPACES)
    assert condition.text == "amount < 5"
    assert condition.find("bpmn:documentation", NAMESPACES).tail == " and approved"
