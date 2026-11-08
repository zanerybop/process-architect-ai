"""Geometry contracts: independent branches, dense lane handovers and saved semantics."""

import json
from itertools import combinations
from pathlib import Path

import pytest
from lxml import etree

from backend.app.domain import Flow, Lane, Node, Participant, Process, ProcessIR
from backend.app.serialization import NS, serialize
from backend.app.xml_validation import validate_xml
from worker.interpreter import interpret

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "examples/cases.json").read_text(encoding="utf-8"))["cases"]
READY = [case for case in CASES if case["expected_status"] == "ready"]


def parse_di(xml):
    root = etree.fromstring(xml.encode())
    boxes = {
        shape.get("bpmnElement"): tuple(float(shape.find("dc:Bounds", NS).get(key))
                                       for key in ("x", "y", "width", "height"))
        for shape in root.findall(".//bpmndi:BPMNShape", NS)
    }
    routes = {
        edge.get("bpmnElement"): [(float(point.get("x")), float(point.get("y")))
                                  for point in edge.findall("di:waypoint", NS)]
        for edge in root.findall(".//bpmndi:BPMNEdge", NS)
    }
    return root, boxes, routes


def assert_clear_geometry(ir, xml):
    root, boxes, routes = parse_di(xml)
    for first, second in combinations(ir.nodes, 2):
        x, y, w, h = boxes[first.id]
        ox, oy, ow, oh = boxes[second.id]
        assert x + w <= ox or ox + ow <= x or y + h <= oy or oy + oh <= y, (first.id, second.id)
    for node in ir.nodes:
        if node.lane_id:
            x, y, w, h = boxes[node.id]
            lx, ly, lw, lh = boxes[node.lane_id]
            assert lx <= x and ly <= y and x + w <= lx + lw and y + h <= ly + lh, node.id
    for flow in ir.flows:
        points = routes[flow.id]
        assert len(points) >= 2, flow.id
        for first, second in zip(points, points[1:]):
            assert first[0] == second[0] or first[1] == second[1], flow.id
            for node in ir.nodes:
                if node.id in (flow.source, flow.target):
                    continue
                x, y, w, h = boxes[node.id]
                if first[0] == second[0]:
                    blocked = (x < first[0] < x + w and min(first[1], second[1]) < y + h
                               and max(first[1], second[1]) > y)
                else:
                    blocked = (y < first[1] < y + h and min(first[0], second[0]) < x + w
                               and max(first[0], second[0]) > x)
                assert not blocked, (flow.id, node.id)
    # Compare graph facts independently from coordinates and serializer helpers.
    emitted = {(element.get("id"), element.get("sourceRef"), element.get("targetRef"))
               for tag in ("sequenceFlow", "messageFlow") for element in root.findall(f".//bpmn:{tag}", NS)}
    assert emitted == {(flow.id, flow.source, flow.target) for flow in ir.flows}


@pytest.mark.parametrize("case", READY, ids=lambda case: case["id"])
def test_corpus_compact_di_preserves_semantics_and_clear_routes(case):
    program = (ROOT / "examples/programs" / f"{case['id']}.py").read_text(encoding="utf-8")
    ir = interpret(program, case["text"])
    before = ir.model_dump_json()
    xml = serialize(ir)
    assert ir.model_dump_json() == before
    assert serialize(ir) == xml
    assert_clear_geometry(ir, xml)
    result = validate_xml(xml)
    assert result["checks"]["xsd"] == "passed"
    assert result["checks"]["layout"] == "passed"


@pytest.mark.parametrize("lane_count", [1, 2, 5])
def test_dense_handoffs_remain_inside_lanes_without_crossing_work(lane_count):
    # A long sequence revisits each lane; transitive same-role precedence must
    # advance the column even when immediate predecessor is in another lane.
    lanes = [Lane(id=f"lane_{i}", name=f"Роль {i}", process_id="process") for i in range(lane_count)]
    nodes = [Node(id="start", type="startEvent", name="Начало", process_id="process", lane_id=lanes[0].id)]
    nodes.extend(Node(id=f"task_{i}", type="task", name=f"Выполнить действие {i}", process_id="process",
                      lane_id=lanes[i % lane_count].id) for i in range(30))
    nodes.append(Node(id="end", type="endEvent", name="Готово", process_id="process", lane_id=lanes[-1].id))
    ir = ProcessIR(processes=[Process(id="process", name="Передача работы")],
                   participants=[Participant(id="pool", name="Организация", process_id="process")],
                   lanes=lanes, nodes=nodes,
                   flows=[Flow(id=f"flow_{i}", source=first.id, target=second.id)
                          for i, (first, second) in enumerate(zip(nodes, nodes[1:]))])
    xml = serialize(ir)
    assert_clear_geometry(ir, xml)
    _, boxes, _ = parse_di(xml)
    assert boxes["pool"][2] < (len(nodes) * 310 + 140) * (0.8 if lane_count == 1 else 0.6)


def test_long_gateway_caption_stays_in_its_lane():
    name = "Необходимо проверить комплектность предоставленной документации и согласование заявки?"
    ir = ProcessIR(processes=[Process(id="process", name="Рассмотрение")],
                   participants=[Participant(id="pool", name="Организация", process_id="process")],
                   lanes=[Lane(id="lane", name="Менеджер", process_id="process")],
                   nodes=[Node(id=id, type=kind, name=name if id == "choice" else id,
                               process_id="process", lane_id="lane") for id, kind in
                          [("start", "startEvent"), ("choice", "exclusiveGateway"),
                           ("end_one", "endEvent"), ("end_two", "endEvent")]],
                   flows=[Flow(id="f1", source="start", target="choice"),
                          Flow(id="f2", source="choice", target="end_one", name="Да", condition="Да"),
                          Flow(id="f3", source="choice", target="end_two", name="Нет", condition="Нет")])
    root, boxes, _ = parse_di(serialize(ir))
    caption = root.xpath("//bpmndi:BPMNShape[@bpmnElement='choice']/bpmndi:BPMNLabel/dc:Bounds",
                         namespaces=NS)[0]
    lane = boxes["lane"]
    assert float(caption.get("y")) + float(caption.get("height")) <= lane[1] + lane[3]
