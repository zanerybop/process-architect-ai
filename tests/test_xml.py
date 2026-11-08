from copy import deepcopy

import pytest
from lxml import etree

from backend.app.domain import Flow, Lane, Node, Participant, Process, ProcessIR
from backend.app.serialization import NS, serialize
from backend.app.xml_validation import MAX_XML_BYTES, validate_xml


def make_ir(node_specs=None, edges=None, with_pool=True):
    node_specs = node_specs or [("start", "startEvent"), ("check", "userTask"), ("end", "endEvent")]
    edges = edges or [("start", "check"), ("check", "end")]
    return ProcessIR(
        processes=[Process(id="process_1", name="Обработка заявки")],
        participants=[Participant(id="pool_1", name="Организация", process_id="process_1")]
        if with_pool
        else [],
        lanes=[Lane(id="lane_1", name="Аналитик", process_id="process_1")],
        nodes=[
            Node(id=node_id, type=node_type, name=node_id, process_id="process_1", lane_id="lane_1")
            for node_id, node_type in node_specs
        ],
        flows=[
            Flow(id=f"f{index}", source=source, target=target) for index, (source, target) in enumerate(edges)
        ],
    )


def xml_tree(xml):
    return etree.fromstring(xml.encode())


def encoded(tree):
    return etree.tostring(tree, encoding="unicode")


def errors(report):
    return [issue for issue in report["issues"] if issue["severity"] == "error"]


def assert_valid(xml, behavior="passed"):
    report = validate_xml(xml)
    assert report["checks"] == {
        "xsd": "passed",
        "graph": "passed",
        "behavior": behavior,
        "layout": "passed",
        "import": "not_run",
    }, report
    assert not errors(report)


@pytest.mark.parametrize("task_type", ["task", "userTask", "serviceTask", "sendTask", "receiveTask"])
def test_p01_all_task_kinds_official_xsd_and_full_di(task_type):
    ir = make_ir()
    ir.nodes[1].type = task_type
    ir.nodes[1].name = 'План & факт <2026>: Доход > Расход, отчёт «Анализ "Север"»'
    before = deepcopy(ir.model_dump())
    xml = serialize(ir)
    assert xml == serialize(ir)
    assert ir.model_dump() == before
    assert_valid(xml)
    root = xml_tree(xml)
    assert root.find(f".//bpmn:{task_type}", NS).get("name") == ir.nodes[1].name
    assert len(root.findall(".//bpmndi:BPMNShape", NS)) == 5
    assert len(root.findall(".//bpmndi:BPMNEdge", NS)) == 2
    assert root.find(".//bpmn:lane/bpmn:flowNodeRef", NS).text == "start"


def xor_ir():
    ir = make_ir(
        [
            ("start", "startEvent"),
            ("choice", "exclusiveGateway"),
            ("approved", "endEvent"),
            ("rejected", "endEvent"),
        ],
        [("start", "choice"), ("choice", "approved"), ("choice", "rejected")],
    )
    ir.flows[1].name = "Согласована"
    ir.flows[1].condition = "Заявка согласована"
    ir.flows[2].name = "Отклонена"
    ir.flows[2].is_default = True
    return ir


def test_xor_default_source_reference_and_multiple_endings():
    xml = serialize(xor_ir())
    assert_valid(xml)
    root = xml_tree(xml)
    assert root.find(".//bpmn:exclusiveGateway", NS).get("default") == "f2"
    default_flow = root.xpath("//bpmn:sequenceFlow[@id='f2']", namespaces=NS)[0]
    assert default_flow.get("default") is None
    assert default_flow.find("bpmn:conditionExpression", NS) is None
    assert len(root.findall(".//bpmn:conditionExpression", NS)) == 1
    assert len(root.findall(".//bpmn:endEvent", NS)) == 2


def parallel_ir():
    ir = make_ir(
        [
            ("start", "startEvent"),
            ("split", "parallelGateway"),
            ("legal", "task"),
            ("finance", "serviceTask"),
            ("join", "parallelGateway"),
            ("end", "endEvent"),
        ],
        [
            ("start", "split"),
            ("split", "legal"),
            ("split", "finance"),
            ("legal", "join"),
            ("finance", "join"),
            ("join", "end"),
        ],
    )
    ir.lanes.append(Lane(id="lane_2", name="Финансовый контролёр", process_id="process_1"))
    ir.nodes[3].lane_id = "lane_2"
    return ir


def test_parallel_lanes_keep_join_and_orthogonal_edges():
    xml = serialize(parallel_ir())
    assert_valid(xml)
    root = xml_tree(xml)
    assert len(root.findall(".//bpmn:parallelGateway", NS)) == 2
    assert not root.findall(".//bpmn:exclusiveGateway", NS)
    assert not root.findall(".//bpmn:conditionExpression", NS)
    for edge in root.findall(".//bpmndi:BPMNEdge", NS):
        points = [(float(point.get("x")), float(point.get("y"))) for point in edge.findall("di:waypoint", NS)]
        assert len(points) >= 2
        assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(points, points[1:]))


def test_cycle_preserves_backward_edge_and_has_distinct_positions():
    ir = make_ir(
        [
            ("start", "startEvent"),
            ("check", "task"),
            ("choice", "exclusiveGateway"),
            ("revise", "task"),
            ("end", "endEvent"),
        ],
        [
            ("start", "check"),
            ("check", "choice"),
            ("choice", "revise"),
            ("revise", "check"),
            ("choice", "end"),
        ],
    )
    ir.flows[2].name = "Неполные"
    ir.flows[4].name = "Полные"
    xml = serialize(ir)
    assert_valid(xml)
    root = xml_tree(xml)
    edge = root.xpath("//bpmn:sequenceFlow[@id='f3']", namespaces=NS)[0]
    assert (edge.get("sourceRef"), edge.get("targetRef")) == ("revise", "check")
    waypoints = root.xpath("//bpmndi:BPMNEdge[@bpmnElement='f3']/di:waypoint", namespaces=NS)
    assert len(waypoints) == 6
    bounds = root.findall(".//bpmndi:BPMNShape/dc:Bounds", NS)
    assert len({(bound.get("x"), bound.get("y")) for bound in bounds}) == len(bounds)


def two_pools_ir():
    ir = make_ir()
    ir.processes.append(Process(id="process_2", name="Поставщик"))
    ir.participants.append(Participant(id="pool_2", name="Поставщик", process_id="process_2"))
    ir.lanes.append(Lane(id="lane_2", name="Менеджер поставщика", process_id="process_2"))
    ir.nodes.extend(
        Node(id=f"supplier_{node_id}", type=node_type, name=node_id, process_id="process_2", lane_id="lane_2")
        for node_id, node_type in [("start", "startEvent"), ("reply", "sendTask"), ("end", "endEvent")]
    )
    ir.flows.extend(
        [
            Flow(id="supplier_f1", source="supplier_start", target="supplier_reply"),
            Flow(id="supplier_f2", source="supplier_reply", target="supplier_end"),
            Flow(id="message_1", kind="message", source="check", target="supplier_reply", name="Запрос цены"),
            Flow(id="message_2", kind="message", source="supplier_reply", target="check", name="Предложение"),
        ]
    )
    return ir


def test_two_pools_message_flows_have_complete_di():
    ir = two_pools_ir()
    xml = serialize(ir)
    assert_valid(xml, behavior="inconclusive")
    root = xml_tree(xml)
    assert len(root.findall(".//bpmndi:BPMNShape", NS)) == 10
    assert len(root.findall(".//bpmndi:BPMNEdge", NS)) == 6
    assert len(root.findall(".//bpmn:messageFlow", NS)) == 2
    assert all(pool.get("processRef") for pool in root.findall(".//bpmn:participant", NS))


def test_message_routes_use_pool_gap_without_crossing_other_nodes():
    ir = two_pools_ir()
    # A later task in the receiving lane would be crossed by a horizontal
    # message return route running across the lane's center line.
    ir.nodes.append(
        Node(id="register", type="task", name="Зарегистрировать", process_id="process_1", lane_id="lane_1")
    )
    ir.flows[1].target = "register"
    ir.flows.append(Flow(id="register_end", source="register", target="end"))
    root = xml_tree(serialize(ir))
    node_ids = {node.id for node in ir.nodes}
    boxes = {
        shape.get("bpmnElement"): tuple(
            float(shape.find("dc:Bounds", NS).get(key)) for key in ("x", "y", "width", "height")
        )
        for shape in root.findall(".//bpmndi:BPMNShape", NS)
        if shape.get("bpmnElement") in node_ids
    }
    for flow in (flow for flow in ir.flows if flow.kind == "message"):
        edge = root.xpath(f"//bpmndi:BPMNEdge[@bpmnElement='{flow.id}']", namespaces=NS)[0]
        points = [(float(point.get("x")), float(point.get("y"))) for point in edge.findall("di:waypoint", NS)]
        sx, sy, sw, sh = boxes[flow.source]
        assert points[0][0] == sx + sw / 2
        assert points[0][1] in (sy, sy + sh)
        for first, second in zip(points, points[1:]):
            for node_id, (x, y, width, height) in boxes.items():
                if node_id in (flow.source, flow.target):
                    continue
                if first[0] == second[0]:
                    assert not (
                        x < first[0] < x + width
                        and min(first[1], second[1]) < y + height
                        and max(first[1], second[1]) > y
                    ), node_id
                else:
                    assert not (
                        y < first[1] < y + height
                        and min(first[0], second[0]) < x + width
                        and max(first[0], second[0]) > x
                    ), node_id


def test_single_process_without_pool_or_lane_is_not_xsd_error():
    ir = make_ir(with_pool=False)
    ir.lanes.clear()
    for node in ir.nodes:
        node.lane_id = None
    xml = serialize(ir)
    assert_valid(xml)
    report = validate_xml(xml)
    assert any(issue["code"] == "ROLE_UNSPECIFIED" for issue in report["issues"])


def test_current_revision_rename_and_coordinates_are_validated_without_relayout():
    root = xml_tree(serialize(make_ir()))
    root.find(".//bpmn:userTask", NS).set("name", "Ручная проверка — изменено")
    bounds = root.xpath("//bpmndi:BPMNShape[@bpmnElement='check']/dc:Bounds", namespaces=NS)[0]
    bounds.set("x", "613.5")
    bounds.set("y", "222.25")
    xml = encoded(root)
    assert_valid(xml)
    assert encoded(root) == xml
    assert bounds.get("x") == "613.5"


@pytest.mark.parametrize(
    "payload,code",
    [
        ('<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>', "XML_DTD_FORBIDDEN"),
        ('<!DOCTYPE x SYSTEM "https://invalid.example/a.dtd"><x/>', "XML_DTD_FORBIDDEN"),
        ("<broken", "INVALID_XML"),
        ("x" * (MAX_XML_BYTES + 1), "XML_TOO_LARGE"),
    ],
    ids=["file_entity", "network_dtd", "malformed", "oversize"],
)
def test_untrusted_xml_rejected(payload, code):
    report = validate_xml(payload)
    assert report["checks"]["xsd"] == "failed"
    assert any(issue["code"] == code for issue in report["issues"])


def test_input_cannot_load_external_schema():
    root = xml_tree(serialize(make_ir()))
    root.set(f"{{{NS['xsi']}}}schemaLocation", f"{NS['bpmn']} https://invalid.example/BPMN20.xsd")
    report = validate_xml(encoded(root))
    assert report["checks"]["xsd"] == "failed"
    assert report["issues"][0]["code"] == "EXTERNAL_XML_REFERENCE"


def test_official_xsd_is_not_permissive():
    root = xml_tree(serialize(make_ir()))
    root.find(".//bpmn:userTask", NS).set("madeUpAttribute", "invalid")
    report = validate_xml(encoded(root))
    assert report["checks"]["xsd"] == "failed"
    assert report["checks"]["graph"] == "passed"
    assert any(issue["code"] == "XSD_INVALID" for issue in report["issues"])


def test_missing_di_is_separate_from_xsd():
    root = xml_tree(serialize(make_ir()))
    shape = root.xpath("//bpmndi:BPMNShape[@bpmnElement='check']", namespaces=NS)[0]
    shape.getparent().remove(shape)
    report = validate_xml(encoded(root))
    assert report["checks"]["xsd"] == "passed"
    assert report["checks"]["layout"] == "failed"
    assert any(issue["code"] == "MISSING_DI" for issue in report["issues"])


def test_nonfinite_di_is_rejected():
    root = xml_tree(serialize(make_ir()))
    root.find(".//dc:Bounds", NS).set("x", "INF")
    report = validate_xml(encoded(root))
    assert report["checks"]["layout"] == "failed"
    assert any(issue["code"] == "INVALID_DI_BOUNDS" for issue in report["issues"])


def test_edited_sequence_cannot_cross_pools_even_when_xsd_passes():
    root = xml_tree(serialize(two_pools_ir()))
    root.xpath("//bpmn:sequenceFlow[@id='f0']", namespaces=NS)[0].set("targetRef", "supplier_reply")
    report = validate_xml(encoded(root))
    assert report["checks"]["xsd"] == "passed"
    assert report["checks"]["graph"] == "failed"


def test_parallel_condition_and_default_condition_are_rejected_after_edit():
    for ir, flow_id in [(parallel_ir(), "f1"), (xor_ir(), "f2")]:
        root = xml_tree(serialize(ir))
        flow = root.xpath(f"//bpmn:sequenceFlow[@id='{flow_id}']", namespaces=NS)[0]
        etree.SubElement(flow, f"{{{NS['bpmn']}}}conditionExpression").text = "Если согласовано"
        report = validate_xml(encoded(root))
        assert report["checks"]["graph"] == "failed"


def test_unknown_lane_reference_fails_graph_without_crashing():
    root = xml_tree(serialize(make_ir()))
    root.find(".//bpmn:flowNodeRef", NS).text = "missing_node"
    report = validate_xml(encoded(root))
    assert report["checks"]["graph"] == "failed"
    assert any(issue["code"] == "INVALID_LANE_REFERENCE" for issue in report["issues"])


def test_standard_loop_is_preserved_with_explicit_behavior_limit():
    root = xml_tree(serialize(make_ir()))
    task = root.find(".//bpmn:userTask", NS)
    etree.SubElement(task, f"{{{NS['bpmn']}}}standardLoopCharacteristics")
    report = validate_xml(encoded(root))
    assert report["checks"]["xsd"] == "passed"
    # The owner expanded the document profile; the loop must not be flattened into
    # a plain task or produce a fabricated successful token-flow check.
    assert report["checks"]["graph"] == "passed"
    assert report["checks"]["behavior"] == "inconclusive"
    assert any(issue["code"] == "BEHAVIOR_ADVANCED_NOT_CHECKED" for issue in report["issues"])


def test_foreign_namespace_reference_cannot_masquerade_as_local_node():
    root = xml_tree(serialize(make_ir()))
    # dc is bound, so the QName is legal XSD syntax but not a local graph reference.
    root.xpath("//bpmndi:BPMNShape[@bpmnElement='check']", namespaces=NS)[0].set("bpmnElement", "dc:check")
    report = validate_xml(encoded(root))
    assert report["checks"]["xsd"] == "passed"
    assert report["checks"]["graph"] == "passed"
    # The semantic graph is unchanged; the diagram's foreign reference is invalid.
    assert report["checks"]["layout"] == "failed"
    assert any(issue["code"] == "INVALID_DI_REFERENCE" for issue in report["issues"])
