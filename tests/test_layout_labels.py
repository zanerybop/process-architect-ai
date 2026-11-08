"""Regressions for observed live DI label collisions; these IRs are hand-authored."""

from itertools import combinations

import pytest
from lxml import etree

from backend.app.domain import Flow, Lane, Node, Participant, Process, ProcessIR
from backend.app.serialization import NS, serialize
from backend.app.xml_validation import validate_xml


def sensor_ir(other_lane=True):
    specs = [
        ("start", "startEvent", "Начало", "tech"),
        ("register", "task", "Зарегистрировать заявку в журнале", "tech"),
        ("check", "task", "Проверить данные прибора", "metrolog"),
        ("xor", "exclusiveGateway", "Данные полные?", "metrolog"),
        ("assign", "task", "Назначить дату поверки", "metrolog"),
        ("end", "endEvent", "Завершено", "metrolog"),
        ("request", "task", "Запросить уточнения", "tech" if other_lane else "metrolog"),
    ]
    return ProcessIR(
        processes=[Process(id="proc", name="Поверка датчика")],
        participants=[Participant(id="pool", name="Поверка датчика", process_id="proc")],
        lanes=[Lane(id=id, name=name, process_id="proc") for id, name in
               [("tech", "Техник"), ("metrolog", "Метролог")]],
        nodes=[Node(id=id, type=kind, name=name, lane_id=lane, process_id="proc")
               for id, kind, name, lane in specs],
        flows=[Flow(id=id, source=source, target=target, name=name, condition=name or None)
               for id, source, target, name in [
                   ("f1", "start", "register", ""),
                   ("f2", "register", "check", ""),
                   ("f3", "check", "xor", ""),
                   ("f4", "xor", "assign", "Данные полные"),
                   ("f5", "assign", "end", ""),
                   ("f6", "xor", "request", "Данные неполные"),
                   ("f7", "request", "register", ""),
               ]],
    )


def box(element):
    return tuple(float(element.get(key)) for key in ("x", "y", "width", "height"))


def overlaps(first, second):
    x, y, width, height = first
    ox, oy, ow, oh = second
    return x < ox + ow and ox < x + width and y < oy + oh and oy < y + height


@pytest.mark.parametrize("other_lane", [True, False])
@pytest.mark.parametrize("label_feedback", [True, False])
def test_branch_labels_do_not_overlap_nodes_gateway_labels_or_each_other(other_lane, label_feedback):
    ir = sensor_ir(other_lane)
    if label_feedback:
        ir.flows[-1].name = "Вернуться к регистрации"
    before = ir.model_dump_json()
    xml = serialize(ir)
    assert serialize(ir) == xml
    assert ir.model_dump_json() == before
    assert validate_xml(xml)["checks"]["xsd"] == "passed"
    root = etree.fromstring(xml.encode())
    labels = [box(item) for item in root.findall(".//bpmndi:BPMNEdge/bpmndi:BPMNLabel/dc:Bounds", NS)]
    assert len(labels) == (3 if label_feedback else 2)
    assert all(not overlaps(first, second) for first, second in combinations(labels, 2))
    node_ids = {node.id for node in ir.nodes}
    obstacles = []
    for shape in root.findall(".//bpmndi:BPMNShape", NS):
        if shape.get("bpmnElement") in node_ids:
            obstacles.append(box(shape.find("dc:Bounds", NS)))
        obstacles.extend(box(item) for item in shape.findall("bpmndi:BPMNLabel/dc:Bounds", NS))
    assert all(not overlaps(label, obstacle) for label in labels for obstacle in obstacles)
    edges = root.findall(".//bpmndi:BPMNEdge", NS)
    for edge in edges:
        label = edge.find("bpmndi:BPMNLabel/dc:Bounds", NS)
        if label is None:
            continue
        for other in edges:
            if other is edge:
                continue
            points = [(float(item.get("x")), float(item.get("y")))
                      for item in other.findall("di:waypoint", NS)]
            for first, second in zip(points, points[1:]):
                segment = (min(first[0], second[0]) - 1, min(first[1], second[1]) - 1,
                           abs(second[0] - first[0]) + 2, abs(second[1] - first[1]) + 2)
                assert not overlaps(box(label), segment), (edge.get("bpmnElement"), other.get("bpmnElement"))
    pool = box(root.xpath("//bpmndi:BPMNShape[@bpmnElement='pool']/dc:Bounds", namespaces=NS)[0])
    assert all(pool[0] <= x and pool[1] <= y and x + w <= pool[0] + pool[2]
               and y + h <= pool[1] + pool[3] for x, y, w, h in labels)

    # Labels must not cause a different route, node position, pool size or graph.
    unlabeled = ir.model_copy(deep=True)
    for flow in unlabeled.flows:
        flow.name = ""
    baseline = etree.fromstring(serialize(unlabeled).encode())
    for path in (".//bpmndi:BPMNShape/dc:Bounds", ".//bpmndi:BPMNEdge/di:waypoint"):
        assert [dict(item.attrib) for item in root.findall(path, NS)] == [
            dict(item.attrib) for item in baseline.findall(path, NS)
        ]
    assert [(item.get("sourceRef"), item.get("targetRef"))
            for item in root.findall(".//bpmn:sequenceFlow", NS)] == [
                (flow.source, flow.target) for flow in ir.flows
            ]
