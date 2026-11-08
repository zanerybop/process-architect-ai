"""Activity I/O is semantic; data edges visually attach to the owning activity."""

from copy import deepcopy

import pytest
from lxml import etree

from backend.app.bpmn_schema import NAMESPACES as NS
from backend.app.document_serialization import arrange_document
from backend.app.document_validation import validate_document_di
from backend.app.serialization import _clear_route
from tests.test_document_layout_regressions import edge_points
from tests.test_document_serialization import assert_xsd, boxes, document, semantic


def io_spec(prefix):
    return f"""<bpmn:ioSpecification id="{prefix}IO">
      <bpmn:dataInput id="{prefix}Input" name="Input"/>
      <bpmn:dataOutput id="{prefix}Output" name="Output"/>
      <bpmn:inputSet id="{prefix}Inputs"><bpmn:dataInputRefs>{prefix}Input</bpmn:dataInputRefs>
      </bpmn:inputSet>
      <bpmn:outputSet id="{prefix}Outputs"><bpmn:dataOutputRefs>{prefix}Output</bpmn:dataOutputRefs>
      </bpmn:outputSet>
    </bpmn:ioSpecification>"""


def data_process(owner="userTask", *, process_io=False, nested=False):
    work = f"""<bpmn:{owner} id="Work" name="Review">{io_spec('Local')}
      <bpmn:dataInputAssociation id="Read"><bpmn:sourceRef>Document</bpmn:sourceRef>
        <bpmn:targetRef>LocalInput</bpmn:targetRef></bpmn:dataInputAssociation>
      <bpmn:dataOutputAssociation id="Write"><bpmn:sourceRef>LocalOutput</bpmn:sourceRef>
        <bpmn:targetRef>Register</bpmn:targetRef></bpmn:dataOutputAssociation>
    </bpmn:{owner}>"""
    flow = f"""<bpmn:startEvent id="Start"/>{work}<bpmn:endEvent id="End"/>
      <bpmn:sequenceFlow id="Incoming" sourceRef="Start" targetRef="Work"/>
      <bpmn:sequenceFlow id="Outgoing" sourceRef="Work" targetRef="End"/>
      <bpmn:dataObject id="Original"/>
      <bpmn:dataObjectReference id="Document" name="Document" dataObjectRef="Original"/>
      <bpmn:dataStoreReference id="Register" name="Register" dataStoreRef="Store"/>"""
    if nested:
        flow = f'<bpmn:subProcess id="Outer">{flow}</bpmn:subProcess>'
    return document(
        '<bpmn:dataStore id="Store"/>'
        f'<bpmn:process id="P">{io_spec("Process") if process_io else ""}{flow}</bpmn:process>'
    )


@pytest.mark.parametrize("owner", ["userTask", "subProcess", "transaction", "adHocSubProcess"])
def test_activity_io_has_no_independent_shapes_but_refs_are_preserved(owner):
    original = data_process(owner)
    before = semantic(original)
    result = arrange_document(original)
    assert_xsd(result)
    assert semantic(original) == before == semantic(result)
    geometry = boxes(result)
    assert "LocalInput" not in geometry and "LocalOutput" not in geometry
    assert {"Document", "Register", "Work"} <= geometry.keys()
    assert result.find('.//bpmn:dataInput[@id="LocalInput"]', NS) is not None
    assert result.find('.//bpmn:dataOutput[@id="LocalOutput"]', NS) is not None
    assert not validate_document_di(result)


@pytest.mark.parametrize("nested", [False, True])
def test_data_associations_dock_perpendicularly_to_activity_away_from_control_flow(nested):
    original = data_process(nested=nested)
    result = arrange_document(original)
    assert_xsd(result)
    assert semantic(original) == semantic(result)
    geometry = boxes(result)
    x, y, width, height = geometry["Work"][0]
    read = edge_points(result, "Read")
    write = edge_points(result, "Write")
    # Both data objects are below the activity. Arrows touch its bottom edge,
    # with a vertical final/initial segment outside, not the incoming-flow port.
    assert x < read[-1][0] < x + width and read[-1][1] == y + height
    assert read[-2][0] == read[-1][0] and read[-2][1] > read[-1][1]
    assert x < write[0][0] < x + width and write[0][1] == y + height
    assert write[1][0] == write[0][0] and write[1][1] > write[0][1]
    assert read[-1] != write[0]
    assert read[-1] != edge_points(result, "Incoming")[-1]
    assert write[0] != edge_points(result, "Outgoing")[0]
    obstacles = [geometry[key][0] for key in ("Work", "Start", "End", "Document", "Register")]
    assert _clear_route(read, obstacles) and _clear_route(write, obstacles)
    work_shape = result.find('.//bpmndi:BPMNShape[@bpmnElement="Work"]', NS).get("id")
    read_di = result.find('.//bpmndi:BPMNEdge[@bpmnElement="Read"]', NS)
    write_di = result.find('.//bpmndi:BPMNEdge[@bpmnElement="Write"]', NS)
    assert read_di.get("targetElement") == write_di.get("sourceElement") == work_shape


def test_process_io_keeps_visible_shapes_while_activity_io_is_hidden():
    original = data_process(process_io=True)
    result = arrange_document(original)
    assert_xsd(result)
    assert semantic(original) == semantic(result)
    geometry = boxes(result)
    assert {"ProcessInput", "ProcessOutput"} <= geometry.keys()
    assert not {"LocalInput", "LocalOutput"} & geometry.keys()
    assert not validate_document_di(result)


def test_existing_explicit_di_is_preserved_until_rearrangement_requested():
    original = arrange_document(data_process())
    # Imported explicit local-parameter DI belongs to the user. Only an explicit
    # rearrangement may replace it; default serialize/import must preserve it.
    plane = original.find("bpmndi:BPMNDiagram/bpmndi:BPMNPlane", NS)
    shape = deepcopy(plane.find("bpmndi:BPMNShape", NS))
    shape.set("id", "UserLocalParameterShape")
    shape.set("bpmnElement", "LocalInput")
    plane.append(shape)
    assert etree.tostring(arrange_document(original)) == etree.tostring(original)
    changed = arrange_document(original, preserve_existing=False)
    assert "LocalInput" not in boxes(changed)
    assert semantic(changed) == semantic(original)


def test_layout_remains_deterministic_with_io():
    source = data_process(process_io=True, nested=True)
    assert etree.tostring(arrange_document(source)) == etree.tostring(arrange_document(source))
