"""Direct DataObject endpoints retain their own DI even when references exist."""

import pytest

from backend.app.bpmn_schema import NAMESPACES as NS
from backend.app.document_serialization import arrange_document
from backend.app.document_validation import validate_document_di
from tests.test_document_serialization import assert_xsd, boxes, document, semantic


def source_document(association, *, reference="Object", metadata=""):
    connections = {
        "read": f"""<bpmn:dataInputAssociation id="Link">
          <bpmn:sourceRef>{reference}</bpmn:sourceRef>
          <bpmn:targetRef>In</bpmn:targetRef></bpmn:dataInputAssociation>""",
        "write": f"""<bpmn:dataOutputAssociation id="Link">
          <bpmn:sourceRef>Out</bpmn:sourceRef>
          <bpmn:targetRef>{reference}</bpmn:targetRef></bpmn:dataOutputAssociation>""",
    }
    ordinary = (
        f'<bpmn:association id="Link" sourceRef="Task" targetRef="{reference}"/>'
        if association == "ordinary" else ""
    )
    return document(f"""<bpmn:process id="P">{metadata}
      <bpmn:task id="Task" name="Work"><bpmn:ioSpecification id="IO">
        <bpmn:dataInput id="In"/><bpmn:dataOutput id="Out"/>
        <bpmn:inputSet id="Inputs"><bpmn:dataInputRefs>In</bpmn:dataInputRefs></bpmn:inputSet>
        <bpmn:outputSet id="Outputs"><bpmn:dataOutputRefs>Out</bpmn:dataOutputRefs></bpmn:outputSet>
      </bpmn:ioSpecification>{connections.get(association, '')}</bpmn:task>
      <bpmn:dataObject id="Object" name="Shared document"/>
      <bpmn:dataObjectReference id="Ref" name="Shared document" dataObjectRef="Object"/>
      {ordinary}
    </bpmn:process>""")


@pytest.mark.parametrize("association", ["read", "write", "ordinary"])
def test_direct_object_endpoint_has_own_shape_and_edge_without_rewriting_refs(association):
    original = source_document(association)
    result = arrange_document(original)
    assert_xsd(result)
    assert semantic(result) == semantic(original)
    geometry = boxes(result)
    assert {"Object", "Ref", "Task"} <= geometry.keys()
    object_shape = result.find('.//bpmndi:BPMNShape[@bpmnElement="Object"]', NS)
    edge = result.find('.//bpmndi:BPMNEdge[@bpmnElement="Link"]', NS)
    assert edge is not None
    side = "sourceElement" if association == "read" else "targetElement"
    assert edge.get(side) == object_shape.get("id")
    assert not validate_document_di(result)


def test_referenced_only_object_still_uses_reference_drawing():
    original = source_document("write", reference="Ref")
    result = arrange_document(original)
    assert_xsd(result)
    assert semantic(result) == semantic(original)
    assert "Object" not in boxes(result)
    assert "Ref" in boxes(result)
    assert not validate_document_di(result)


def test_metadata_reference_does_not_force_extra_object_shape():
    original = source_document(
        "write", reference="Ref",
        metadata='''<bpmn:extensionElements><ext:payload xmlns:ext="urn:metadata">
          <bpmn:association id="MetadataLink" sourceRef="Task" targetRef="Object"/>
          </ext:payload></bpmn:extensionElements>''',
    )
    result = arrange_document(original)
    assert_xsd(result)
    assert semantic(result) == semantic(original)
    assert "Object" not in boxes(result)


def test_qualified_local_endpoint_keeps_object_shape():
    original = source_document("ordinary", reference="bpmn:Object")
    result = arrange_document(original)
    assert_xsd(result)
    assert semantic(result) == semantic(original)
    assert "Object" in boxes(result)
    assert not validate_document_di(result)


def test_foreign_namespace_endpoint_does_not_alias_local_object():
    original = source_document("ordinary", reference="foreign:Object")
    # QName is on the individual association to prevent accidental use of a
    # matching local ID from a foreign namespace.
    from lxml import etree

    edge = original.find('.//bpmn:association', NS)
    replacement = etree.Element(edge.tag, attrib=dict(edge.attrib), nsmap={"foreign": "urn:other"})
    edge.getparent().replace(edge, replacement)
    result = arrange_document(original)
    assert "Object" not in boxes(result)
    assert result.find('.//bpmndi:BPMNEdge[@bpmnElement="Link"]', NS) is None
    assert semantic(result) == semantic(original)
