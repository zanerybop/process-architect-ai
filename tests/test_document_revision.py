"""Full semantic revision diffs cannot flatten advanced attributes or containment."""

from copy import deepcopy

from lxml import etree

from backend.app.document_serialization import arrange_document, build_document_tree
from backend.app.document_validation import NS
from backend.app.domain import BpmnDocument, BpmnElement, ProcessIR
from backend.app.revision_review import review_revision


def original():
    elements = [
        BpmnElement(
            key="d", tag="bpmn:definitions", attributes={"id": "Defs", "targetNamespace": "urn:test"}
        ),
        BpmnElement(key="p", tag="bpmn:process", parent="d", attributes={"id": "P"}),
        BpmnElement(
            key="t",
            tag="bpmn:serviceTask",
            parent="p",
            attributes={"id": "T", "name": "Prepare", "implementation": "urn:service"},
        ),
        BpmnElement(
            key="b",
            tag="bpmn:boundaryEvent",
            parent="p",
            attributes={"id": "B", "attachedToRef": "T", "cancelActivity": "false"},
        ),
        BpmnElement(key="timer", tag="bpmn:timerEventDefinition", parent="b"),
        BpmnElement(key="duration", tag="bpmn:timeDuration", parent="timer", text="PT10M"),
        BpmnElement(key="end", tag="bpmn:endEvent", parent="p", attributes={"id": "E"}),
        BpmnElement(
            key="f",
            tag="bpmn:sequenceFlow",
            parent="p",
            attributes={"id": "F", "sourceRef": "B", "targetRef": "E"},
        ),
    ]
    ir = ProcessIR(schema_version="2.0", document=BpmnDocument(elements=elements))
    root = build_document_tree(ir)
    root = arrange_document(root)
    return ir, root


def review(ir, root):
    return review_revision(etree.tostring(root, encoding="unicode"), {"ir": ir.model_dump()})


def test_document_geometry_only_revision_keeps_semantics_current():
    ir, root = original()
    before = ir.model_dump_json()
    bounds = root.find(".//dc:Bounds", NS)
    bounds.set("x", "123.25")
    report = review(ir, root)
    assert report["checks"]["xsd"] == report["checks"]["graph"] == report["checks"]["layout"] == "passed"
    assert report["semantic_diff"]["status"] == "unchanged"
    assert report["semantic_diff"]["geometry_only"] is True
    assert ir.model_dump_json() == before


def test_timer_expression_change_is_semantic_even_without_activity_rename():
    ir, root = original()
    root.find(".//bpmn:timeDuration", NS).text = "PT30M"
    report = review(ir, root)
    assert report["semantic_diff"]["status"] == "changed"
    assert any("text" in item["fields"] for item in report["semantic_diff"]["changes"])


def test_task_implementation_change_is_semantic():
    ir, root = original()
    root.find(".//bpmn:serviceTask", NS).set("implementation", "urn:other")
    report = review(ir, root)
    assert report["semantic_diff"]["status"] == "changed"
    assert any(item["element_id"] == "T" for item in report["semantic_diff"]["changes"])


def test_default_attribute_and_namespace_prefix_do_not_fabricate_semantic_change():
    ir, root = original()
    root.set("exporter", "bpmn-js")
    root.find(".//bpmn:serviceTask", NS).set("isForCompensation", "0")
    report = review(ir, root)
    assert report["semantic_diff"]["status"] == "unchanged"


def test_whole_document_mixed_content_is_part_of_semantic_snapshot():
    from backend.app.document_validation import semantic_snapshot

    root = etree.fromstring(
        f'<definitions xmlns="{NS["bpmn"]}" xmlns:e="urn:extension" targetNamespace="urn:test">'
        '<process id="P"><documentation>before<e:span>middle</e:span>after</documentation></process>'
        "</definitions>"
    )
    altered = deepcopy(root)
    altered.find(".//{urn:extension}span").tail = "changed"
    assert semantic_snapshot(root) != semantic_snapshot(altered)
