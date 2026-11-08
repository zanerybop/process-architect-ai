"""Full-document XML preservation and hierarchical DI independent of legacy IR."""

from copy import deepcopy
from pathlib import Path

import pytest
from lxml import etree

from backend.app.bpmn_schema import NAMESPACES as NS
from backend.app.document_serialization import arrange_document, build_document_tree, serialize_document
from backend.app.domain import BpmnDocument, BpmnElement, PipelineError, ProcessIR
from backend.app.serialization import _clear_route
from backend.app.xml_validation import _schema
from worker.interpreter import interpret


def document(body):
    return etree.fromstring(
        (
            '<bpmn:definitions xmlns:bpmn="'
            + NS["bpmn"]
            + '" xmlns:xsi="'
            + NS["xsi"]
            + '" id="Definitions" targetNamespace="urn:test">'
            + body
            + "</bpmn:definitions>"
        ).encode()
    )


def ir_from(root):
    elements = []

    def walk(node, parent=None):
        key = f"key_{len(elements)}"
        values = dict(key=key, tag=node.tag, parent=parent, attributes=dict(node.attrib), text=node.text)
        if "tail" in BpmnElement.model_fields:
            values["tail"] = node.tail
        elements.append(BpmnElement(**values))
        for child in node:
            walk(child, key)

    walk(root)
    return ProcessIR(
        schema_version="2.0",
        document=BpmnDocument(elements=elements, namespaces={k or "": v for k, v in root.nsmap.items()}),
    )


def semantic(root):
    copied = deepcopy(root)
    for diagram in copied.findall("bpmndi:BPMNDiagram", NS):
        copied.remove(diagram)

    # Compare namespaces/values and order, not namespace declaration placement.
    def snapshot(item):
        return item.tag, tuple(sorted(item.attrib.items())), item.text, item.tail, tuple(map(snapshot, item))

    return snapshot(copied)


def boxes(root):
    result = {}
    for shape in root.findall(".//bpmndi:BPMNShape", NS):
        bounds = shape.find("dc:Bounds", NS)
        result.setdefault(shape.get("bpmnElement"), []).append(
            tuple(float(bounds.get(name)) for name in ("x", "y", "width", "height"))
        )
    return result


def inside(child, parent):
    x, y, width, height = child
    px, py, pw, ph = parent
    return px <= x and py <= y and x + width <= px + pw and y + height <= py + ph


def assert_xsd(root):
    schema = _schema()
    assert schema.validate(root), str(schema.error_log)


def test_document_build_orders_schema_children_and_preserves_non_graph_content():
    root = document("""<bpmn:process id="P">
      <bpmn:serviceTask id="T" implementation="urn:service">
      <bpmn:standardLoopCharacteristics testBefore="true">
      <bpmn:loopCondition xsi:type="bpmn:tFormalExpression">count &lt; 3</bpmn:loopCondition>
      </bpmn:standardLoopCharacteristics>
      <bpmn:documentation>Точное описание &amp; условия</bpmn:documentation>
      </bpmn:serviceTask>
      </bpmn:process>
      <bpmn:message id="Message" name="Ответ"/>""")
    ir = ir_from(root)
    before = ir.model_dump_json()
    result = build_document_tree(ir)
    assert ir.model_dump_json() == before
    task = result.find(".//bpmn:serviceTask", NS)
    assert etree.QName(task[0]).localname == "documentation"
    assert task.get("implementation") == "urn:service"
    assert task.find(".//bpmn:loopCondition", NS).text == "count < 3"
    assert result.find("bpmn:message", NS) is not None
    assert result.find("bpmndi:BPMNDiagram", NS) is None
    assert_xsd(result)


def test_nested_containers_boundary_and_event_subprocess_preserve_semantics():
    root = document("""
  <bpmn:process id="P">
    <bpmn:startEvent id="Start"/>
    <bpmn:subProcess id="Sub" name="Проверить запрос">
      <bpmn:startEvent id="SubStart"/>
      <bpmn:transaction id="Tx">
        <bpmn:startEvent id="TxStart"/>
        <bpmn:manualTask id="Work" name="Подготовить"/>
        <bpmn:endEvent id="TxEnd"/>
        <bpmn:sequenceFlow id="Tf1" sourceRef="TxStart" targetRef="Work"/>
        <bpmn:sequenceFlow id="Tf2" sourceRef="Work" targetRef="TxEnd"/>
      </bpmn:transaction>
      <bpmn:endEvent id="SubEnd"/>
      <bpmn:sequenceFlow id="Sf1" sourceRef="SubStart" targetRef="Tx"/>
      <bpmn:sequenceFlow id="Sf2" sourceRef="Tx" targetRef="SubEnd"/>
    </bpmn:subProcess>
    <bpmn:boundaryEvent
        id="Timeout"
        attachedToRef="Sub"
        cancelActivity="false"
        name="Срок истёк">
      <bpmn:timerEventDefinition id="Timer">
        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">PT5M</bpmn:timeDuration>
      </bpmn:timerEventDefinition>
    </bpmn:boundaryEvent>
    <bpmn:endEvent id="Expired"/>
    <bpmn:endEvent id="End"/>
    <bpmn:subProcess id="EventSub" triggeredByEvent="true">
      <bpmn:startEvent id="EventStart" isInterrupting="false">
        <bpmn:signalEventDefinition id="SignalDef" signalRef="Signal"/>
      </bpmn:startEvent>
      <bpmn:scriptTask id="Handle" scriptFormat="text/plain">
        <bpmn:script>Literal body only</bpmn:script>
      </bpmn:scriptTask>
      <bpmn:endEvent id="EventEnd"/>
      <bpmn:sequenceFlow id="Ef1" sourceRef="EventStart" targetRef="Handle"/>
      <bpmn:sequenceFlow id="Ef2" sourceRef="Handle" targetRef="EventEnd"/>
    </bpmn:subProcess>
    <bpmn:sequenceFlow id="f1" sourceRef="Start" targetRef="Sub"/>
    <bpmn:sequenceFlow id="f2" sourceRef="Sub" targetRef="End"/>
    <bpmn:sequenceFlow id="f3" sourceRef="Timeout" targetRef="Expired"/>
  </bpmn:process>
  <bpmn:signal id="Signal"/>
""")
    before = semantic(root)
    result = arrange_document(root)
    assert semantic(root) == before == semantic(result)
    assert etree.tostring(result) == etree.tostring(arrange_document(root))
    assert_xsd(result)
    geometry = boxes(result)
    assert inside(geometry["Work"][0], geometry["Tx"][0])
    assert inside(geometry["Tx"][0], geometry["Sub"][0])
    assert inside(geometry["Handle"][0], geometry["EventSub"][0])
    bx, by, bw, bh = geometry["Timeout"][0]
    sx, sy, sw, sh = geometry["Sub"][0]
    assert sy + sh == by + bh / 2 and sx < bx < sx + sw
    boundary_edge = result.find('.//bpmndi:BPMNEdge[@bpmnElement="f3"]', NS)
    points = [
        (float(point.get("x")), float(point.get("y"))) for point in boundary_edge.findall("di:waypoint", NS)
    ]
    assert _clear_route(points, [geometry["Sub"][0]])
    assert result.find(".//bpmn:script", NS).text == "Literal body only"


def test_nested_lanes_and_multiple_diagram_roots():
    root = document("""<bpmn:process id="P">
      <bpmn:laneSet id="Lanes">
      <bpmn:lane id="ParentLane" name="Отдел">
      <bpmn:childLaneSet id="ChildLanes">
      <bpmn:lane id="LeafA" name="Аналитик">
      <bpmn:flowNodeRef>A</bpmn:flowNodeRef>
      </bpmn:lane>
      <bpmn:lane id="LeafB" name="Оператор">
      <bpmn:flowNodeRef>B</bpmn:flowNodeRef>
      </bpmn:lane>
      </bpmn:childLaneSet>
      </bpmn:lane>
      </bpmn:laneSet>
      <bpmn:task id="A"/>
      <bpmn:task id="B"/>
      <bpmn:sequenceFlow id="F" sourceRef="A" targetRef="B"/>
      </bpmn:process>
      <bpmn:process id="Independent">
      <bpmn:adHocSubProcess id="AdHoc">
      <bpmn:manualTask id="AdHocTask"/>
      <bpmn:completionCondition xsi:type="bpmn:tFormalExpression">done</bpmn:completionCondition>
      </bpmn:adHocSubProcess>
      </bpmn:process>
      <bpmn:collaboration id="C">
      <bpmn:participant id="Pool" processRef="P"/>
      <bpmn:participant id="BlackBox" name="Партнёр"/>
      <bpmn:messageFlow id="MessageFlow" sourceRef="A" targetRef="BlackBox"/>
      </bpmn:collaboration>""")
    result = arrange_document(root)
    assert_xsd(result)
    planes = result.findall("bpmndi:BPMNDiagram/bpmndi:BPMNPlane", NS)
    assert {plane.get("bpmnElement") for plane in planes} == {"C", "Independent"}
    geometry = boxes(result)
    for child, parent in [
        ("A", "LeafA"),
        ("B", "LeafB"),
        ("LeafA", "ParentLane"),
        ("LeafB", "ParentLane"),
        ("ParentLane", "Pool"),
        ("AdHocTask", "AdHoc"),
    ]:
        assert inside(geometry[child][0], geometry[parent][0]), (child, parent)
    assert result.find('.//bpmndi:BPMNEdge[@bpmnElement="MessageFlow"]', NS) is not None


def test_data_artifacts_and_associations_are_not_erased_or_used_as_sequence_nodes():
    root = document("""
  <bpmn:process id="P">
    <bpmn:task id="Task">
      <bpmn:ioSpecification>
        <bpmn:dataInput id="In"/>
        <bpmn:dataOutput id="Out"/>
        <bpmn:inputSet id="Inputs">
          <bpmn:dataInputRefs>In</bpmn:dataInputRefs>
        </bpmn:inputSet>
        <bpmn:outputSet id="Outputs">
          <bpmn:dataOutputRefs>Out</bpmn:dataOutputRefs>
        </bpmn:outputSet>
      </bpmn:ioSpecification>
      <bpmn:dataInputAssociation id="Read">
        <bpmn:sourceRef>Ref</bpmn:sourceRef>
        <bpmn:targetRef>In</bpmn:targetRef>
      </bpmn:dataInputAssociation>
      <bpmn:dataOutputAssociation id="Write">
        <bpmn:sourceRef>Out</bpmn:sourceRef>
        <bpmn:targetRef>StoreRef</bpmn:targetRef>
      </bpmn:dataOutputAssociation>
    </bpmn:task>
    <bpmn:dataObject id="Object" isCollection="true"/>
    <bpmn:dataObjectReference id="Ref" dataObjectRef="Object" name="Входные данные"/>
    <bpmn:dataStoreReference id="StoreRef" dataStoreRef="Store"/>
    <bpmn:textAnnotation id="Note">
      <bpmn:text>Результат сохраняется.</bpmn:text>
    </bpmn:textAnnotation>
    <bpmn:association
        id="Explain"
        sourceRef="Task"
        targetRef="Note"
        associationDirection="None"/>
    <bpmn:group id="Group" categoryValueRef="CategoryValue"/>
  </bpmn:process>
  <bpmn:dataStore id="Store"/>
  <bpmn:category id="Category">
    <bpmn:categoryValue id="CategoryValue" value="Архив"/>
  </bpmn:category>
""")
    before = semantic(root)
    result = arrange_document(root)
    assert semantic(result) == before
    assert_xsd(result)
    geometry = boxes(result)
    assert {"Task", "Ref", "StoreRef", "Note", "Group"} <= geometry.keys()
    assert not {"In", "Out"} & geometry.keys()
    assert result.find('.//bpmn:dataInput[@id="In"]', NS) is not None
    assert result.find('.//bpmn:dataOutput[@id="Out"]', NS) is not None
    assert "Object" not in geometry and "Store" not in geometry
    assert {edge.get("bpmnElement") for edge in result.findall(".//bpmndi:BPMNEdge", NS)} == {
        "Read",
        "Write",
        "Explain",
    }


def test_choreography_bands_and_conversation_are_separate_real_shapes():
    root = document("""<bpmn:choreography id="Choreo">
      <bpmn:participant id="Buyer" name="Покупатель"/>
      <bpmn:participant id="Seller" name="Продавец"/>
      <bpmn:messageFlow id="Request" sourceRef="Buyer" targetRef="Seller"/>
      <bpmn:messageFlow id="Reply" sourceRef="Seller" targetRef="Buyer"/>
      <bpmn:choreographyTask id="Exchange" name="Обмен" initiatingParticipantRef="Buyer">
        <bpmn:participantRef>Buyer</bpmn:participantRef>
      <bpmn:participantRef>Seller</bpmn:participantRef>
      <bpmn:messageFlowRef>Request</bpmn:messageFlowRef>
      </bpmn:choreographyTask>
      <bpmn:choreographyTask id="Respond" initiatingParticipantRef="Seller">
        <bpmn:participantRef>Seller</bpmn:participantRef>
      <bpmn:participantRef>Buyer</bpmn:participantRef>
      <bpmn:messageFlowRef>Reply</bpmn:messageFlowRef>
      </bpmn:choreographyTask>
      <bpmn:sequenceFlow id="Next" sourceRef="Exchange" targetRef="Respond"/>
      </bpmn:choreography>
      <bpmn:collaboration id="Conversation">
      <bpmn:participant id="A"/>
      <bpmn:participant id="B"/>
      <bpmn:conversation id="Topic" name="Условия">
      <bpmn:participantRef>A</bpmn:participantRef>
      <bpmn:participantRef>B</bpmn:participantRef>
      </bpmn:conversation>
      <bpmn:conversationLink id="CL1" sourceRef="A" targetRef="Topic"/>
      <bpmn:conversationLink id="CL2" sourceRef="B" targetRef="Topic"/>
      </bpmn:collaboration>""")
    result = arrange_document(root)
    assert_xsd(result)
    assert semantic(result) == semantic(root)
    assert len(result.findall("bpmndi:BPMNDiagram", NS)) == 2
    bands = result.xpath("//bpmndi:BPMNShape[@participantBandKind]", namespaces=NS)
    assert len(bands) == 4
    assert len({shape.get("id") for shape in bands}) == 4
    assert {shape.get("bpmnElement") for shape in bands} == {"Buyer", "Seller"}
    assert all(shape.get("choreographyActivityShape") for shape in bands)
    assert "Topic" in boxes(result)
    assert {"Next", "Request", "Reply", "CL1", "CL2"} <= {
        edge.get("bpmnElement") for edge in result.findall(".//bpmndi:BPMNEdge", NS)
    }


def test_existing_di_preserved_and_explicit_relayout_does_not_change_semantics():
    original = arrange_document(
        document('<bpmn:process id="P"><bpmn:task id="T" name="Работа"/></bpmn:process>')
    )
    original.find(".//dc:Bounds", NS).set("x", "987.25")
    saved = etree.tostring(original)
    assert etree.tostring(arrange_document(original)) == saved
    rebuilt = arrange_document(original, preserve_existing=False)
    assert etree.tostring(original) == saved
    assert semantic(rebuilt) == semantic(original)
    assert rebuilt.find(".//dc:Bounds", NS).get("x") != "987.25"
    assert_xsd(rebuilt)


def test_serialize_document_is_deterministic_and_does_not_mutate_ir():
    ir = ir_from(
        document('<bpmn:process id="P"><bpmn:businessRuleTask id="T" name="Правило"/></bpmn:process>')
    )
    before = ir.model_dump_json()
    xml = serialize_document(ir)
    assert xml == serialize_document(ir)
    assert ir.model_dump_json() == before
    assert_xsd(etree.fromstring(xml.encode()))


def test_parent_cycle_is_an_error_not_a_partial_document():
    ir = ir_from(document('<bpmn:process id="P"/>'))
    ir.document.elements.extend(
        [BpmnElement(key="x", tag="bpmn:task", parent="y"), BpmnElement(key="y", tag="bpmn:task", parent="x")]
    )
    with pytest.raises(PipelineError, match="недостижима"):
        build_document_tree(ir)


def test_xsi_type_polymorphism_is_drawn_without_rewriting_element_names():
    root = document("""
  <bpmn:rootElement xsi:type="bpmn:tProcess" id="P">
    <bpmn:flowElement xsi:type="bpmn:tSubProcess" id="Sub">
      <bpmn:flowElement xsi:type="bpmn:tUserTask" id="Task" name="Исполнитель"/>
    </bpmn:flowElement>
    <bpmn:flowElement xsi:type="bpmn:tInclusiveGateway" id="Choice"/>
    <bpmn:flowElement
        xsi:type="bpmn:tSequenceFlow"
        id="Flow"
        sourceRef="Sub"
        targetRef="Choice"/>
  </bpmn:rootElement>
""")
    result = arrange_document(root)
    assert_xsd(result)
    assert semantic(root) == semantic(result)
    geometry = boxes(result)
    assert {"Sub", "Task", "Choice"} <= geometry.keys()
    assert inside(geometry["Task"][0], geometry["Sub"][0])
    assert result.find('.//bpmndi:BPMNEdge[@bpmnElement="Flow"]', NS) is not None


def test_foreign_qname_does_not_connect_to_a_coinciding_local_id():
    root = document("""<bpmn:process id="P" xmlns:external="urn:other-document">
      <bpmn:task id="A"/><bpmn:task id="B"/>
      <bpmn:sequenceFlow id="Flow" sourceRef="A" targetRef="external:B"/>
    </bpmn:process>""")
    result = arrange_document(root)
    assert semantic(root) == semantic(result)
    assert result.find('.//bpmndi:BPMNEdge[@bpmnElement="Flow"]', NS) is None


def test_mixed_documentation_and_foreign_extension_order_are_preserved():
    root = document("""
  <bpmn:process id="P">
    <bpmn:documentation
        xmlns:h="http://www.w3.org/1999/xhtml">До <h:b>важного</h:b> после.</bpmn:documentation>
    <bpmn:extensionElements xmlns:meta="urn:metadata">
      <meta:item key="one">Данные</meta:item>
    </bpmn:extensionElements>
    <bpmn:task id="Task"/>
  </bpmn:process>
""")
    result = build_document_tree(ir_from(root))
    assert semantic(root) == semantic(result)
    assert_xsd(result)


def test_relayout_preserves_imported_comments_and_processing_instructions():
    root = document("""<!-- An imported diagram --><?review approved="false"?>
      <bpmn:process id="Process"><!-- Inner note --><bpmn:task id="Task"/></bpmn:process>
      <!-- End of semantic content -->""")
    result = arrange_document(root)
    assert_xsd(result)
    assert [item.text for item in result.xpath("//comment()")] == [
        " An imported diagram ",
        " Inner note ",
        " End of semantic content ",
    ]
    assert result.xpath("//processing-instruction()")[0].text == 'approved="false"'
    assert "Task" in boxes(result)


def test_transaction_fixture_branch_labels_do_not_cross_routes():
    directory = Path("examples/full_bpmn/F02_transaction_compensation")
    ir = interpret(
        (directory / "process.py").read_text(encoding="utf-8"),
        (directory / "source.txt").read_text(encoding="utf-8"),
    )
    result = etree.fromstring(serialize_document(ir).encode())
    assert_xsd(result)
    edges = result.findall(".//bpmndi:BPMNEdge", NS)
    labelled_branches = []
    for edge in edges:
        bounds = edge.find("bpmndi:BPMNLabel/dc:Bounds", NS)
        if bounds is None:
            continue
        labelled_branches.append(edge.get("bpmnElement"))
        box = tuple(float(bounds.get(name)) for name in ("x", "y", "width", "height"))
        for other in edges:
            points = [
                (float(point.get("x")), float(point.get("y"))) for point in other.findall("di:waypoint", NS)
            ]
            assert _clear_route(points, [box]), (edge.get("bpmnElement"), other.get("bpmnElement"))
    assert set(labelled_branches) == {"t4", "t5"}


def test_bpmn_looking_metadata_is_preserved_without_rendering_or_resolving_it():
    root = document("""
      <bpmn:process id="Process">
        <bpmn:documentation>Example only:
          <bpmn:sequenceFlow id="HiddenDocFlow" sourceRef="A" targetRef="B"/>
        </bpmn:documentation>
        <bpmn:extensionElements>
          <meta:sample xmlns:meta="urn:metadata">
            <bpmn:sequenceFlow id="HiddenExtensionFlow" sourceRef="A" targetRef="B"/>
            <bpmn:task id="HiddenTask"/>
          </meta:sample>
        </bpmn:extensionElements>
        <bpmn:task id="A"/><bpmn:task id="B"/>
        <bpmn:sequenceFlow id="RealFlow" sourceRef="A" targetRef="B"/>
        <bpmn:association id="InvalidRef" sourceRef="A" targetRef="HiddenTask"/>
      </bpmn:process>
    """)
    result = arrange_document(root)
    assert semantic(result) == semantic(root)
    drawn = {item.get("bpmnElement") for item in result.findall(".//bpmndi:BPMNEdge", NS)}
    assert drawn == {"RealFlow"}
    assert "HiddenTask" not in boxes(result)
