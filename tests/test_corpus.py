"""Offline verification of explicitly hand-authored fixtures, not LLM evaluation.

These repository-owned synthetic programs call the same bounded interpreter as the
worker. Isolation/infrastructure checks are separate; no runtime fallback is added.
"""

import json
from pathlib import Path

import pytest
from lxml import etree

from backend.app.builder import TASK_TYPES
from backend.app.graph import validate_graph
from backend.app.serialization import NS, serialize
from backend.app.xml_validation import validate_xml
from worker.interpreter import interpret

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "examples/cases.json").read_text(encoding="utf-8"))["cases"]
BY_ID = {case["id"]: case for case in CASES}
READY = [case for case in CASES if case["expected_status"] == "ready"]

# Independent hand-reviewed semantic expectation: no extra business action may
# enter a fixture just to make its topology pass validation.
EXPECTED_ACTIONS = {
    "P01": {"Проверить заявку", "Зарегистрировать заявку в журнале"},
    "P02": {"Подать заявку", "Рассмотреть заявку", "Оформить заказ", "Получить уведомление об отказе"},
    "P03": {"Зарегистрировать комплект документов", "Проверить договор", "Проверить бюджет",
            "Сформировать сводное заключение"},
    "P04": {"Направить документы", "Проверить комплектность", "Дополнить комплект",
            "Зарегистрировать документы"},
    "P05": {"Создать заявку на закупку запасных частей для планового ремонта",
            "Проверить комплектность заявки", "Дополнить заявку", "Проверить наличие и условия поставки",
            "Проверить бюджет", "Принять решение", "Оформить заказ", "Получить уведомление об отказе"},
    "P06": {"Принять обращение", "Определить тип обращения", "Подготовить ответ на технический вопрос",
            "Подготовить ответ по документам", "Подготовить ответ на жалобу"},
    "P10": {"Отправить запрос цены поставщику", "Получить предложение", "Зарегистрировать предложение",
            "Получить запрос", "Подготовить предложение", "Отправить предложение заказчику"},
    "P11": {"Получить показание счётчика", "Занести показание в журнал"},
    "P12": {"Получить заявку", "Зарегистрировать заявку"},
    "P13": {"Получить файл «План & факт <2026>»", "Проверить поле «Доход > Расход»",
            'Сохранить результат в отчёт «Анализ "Север"»'},
}


def load_ir(case_id):
    code = (ROOT / "examples/programs" / f"{case_id}.py").read_text(encoding="utf-8")
    return interpret(code, BY_ID[case_id]["text"])


def reachable(ir, start, excluded=()):
    seen, pending = set(), [start]
    while pending:
        node_id = pending.pop()
        if node_id in seen or node_id in excluded:
            continue
        seen.add(node_id)
        pending.extend(flow.target for flow in ir.flows if flow.kind == "sequence" and flow.source == node_id)
    return seen


@pytest.mark.parametrize("case", READY, ids=lambda case: case["id"])
def test_trusted_fixture_core_graph_and_official_xsd(case):
    ir = load_ir(case["id"])
    assert not [issue for issue in validate_graph(ir) if issue.severity == "error"]
    before = ir.model_dump_json()
    xml = serialize(ir)
    assert ir.model_dump_json() == before, "Layout/serialization must not rewrite the IR"
    assert serialize(ir) == xml, "Fixed IR must produce deterministic XML and DI"
    result = validate_xml(xml)
    assert result["checks"]["xsd"] == "passed", result
    assert result["checks"]["layout"] == "passed", result
    assert result["checks"]["import"] == "not_run", "XSD does not prove bpmn-js import"

    root = etree.fromstring(xml.encode())
    semantic_nodes = {element.get("id"): etree.QName(element).localname
                      for process in root.findall("bpmn:process", NS)
                      for element in process if etree.QName(element).localname in TASK_TYPES
                      | {"startEvent", "endEvent", "exclusiveGateway", "parallelGateway"}}
    assert semantic_nodes == {node.id: node.type for node in ir.nodes}
    xml_flows = {(element.get("id"), element.get("sourceRef"), element.get("targetRef"))
                 for element in root.findall(".//bpmn:sequenceFlow", NS)
                 + root.findall(".//bpmn:messageFlow", NS)}
    assert xml_flows == {(flow.id, flow.source, flow.target) for flow in ir.flows}
    shapes = {shape.get("bpmnElement") for shape in root.findall(".//bpmndi:BPMNShape", NS)}
    assert shapes == {item.id for item in [*ir.nodes, *ir.lanes, *ir.participants]}
    edges = {edge.get("bpmnElement") for edge in root.findall(".//bpmndi:BPMNEdge", NS)}
    assert edges == {flow.id for flow in ir.flows}
    for lane in ir.lanes:
        references = root.xpath(".//bpmn:lane[@id=$id]/bpmn:flowNodeRef/text()", namespaces=NS, id=lane.id)
        assert set(references) == {node.id for node in ir.nodes if node.lane_id == lane.id}


@pytest.mark.parametrize("case", READY, ids=lambda case: case["id"])
def test_fixture_preserves_roles_actions_and_exact_quotes(case):
    ir = load_ir(case["id"])
    if "roles" in case["expected"]:
        assert {lane.name for lane in ir.lanes} == set(case["expected"]["roles"])
    actions = [node for node in ir.nodes if node.type in TASK_TYPES]
    assert {node.name for node in actions} == EXPECTED_ACTIONS[case["id"]]
    assert len(actions) == len(EXPECTED_ACTIONS[case["id"]]), "No duplicate invented business actions"
    assert not ir.assumptions
    provenance = {entry.element_id: entry for entry in ir.provenance}
    for action in actions:
        entry = provenance[action.id]
        assert entry.origin == "explicit"
        assert entry.exact_quote in case["text"]
        assert case["text"][entry.source_start:entry.source_end] == entry.exact_quote
    for flow in ir.flows:
        if flow.condition:
            assert flow.condition in case["text"]


@pytest.mark.parametrize("case_id", ["P01", "P11", "P12", "P13"])
def test_linear_cases_have_no_unfounded_branches(case_id):
    ir = load_ir(case_id)
    assert not [node for node in ir.nodes if node.type.endswith("Gateway")]
    assert len(ir.flows) == len(ir.nodes) - 1


@pytest.mark.parametrize("case_id,branches,following", [
    ("P03", ("p03_contract", "p03_budget"), "p03_conclusion"),
    ("P05", ("p05_availability", "p05_budget"), "p05_decide"),
])
def test_parallel_checks_both_join_before_following_action(case_id, branches, following):
    ir = load_ir(case_id)
    prefix = case_id.lower()
    split, join = f"{prefix}_split", f"{prefix}_join"
    nodes = {node.id: node for node in ir.nodes}
    assert nodes[split].type == nodes[join].type == "parallelGateway"
    assert {flow.target for flow in ir.flows if flow.source == split} == set(branches)
    assert {flow.source for flow in ir.flows if flow.target == join} == set(branches)
    assert following in reachable(ir, join)
    for branch in branches:
        assert following not in reachable(ir, branch, excluded={join})
        assert set(branches) - {branch} <= set(nodes) - reachable(ir, branch)
    assert not [flow for flow in ir.flows if flow.source == split and (flow.condition or flow.is_default)]


@pytest.mark.parametrize("case_id,following", [("P04", "p04_register"), ("P05", "p05_split")])
def test_rework_returns_to_check_before_continuing(case_id, following):
    ir = load_ir(case_id)
    prefix = case_id.lower()
    check, rework = f"{prefix}_check", f"{prefix}_rework"
    assert check in reachable(ir, rework)
    assert rework in reachable(ir, check)
    assert following in reachable(ir, rework)
    assert following not in reachable(ir, rework, excluded={check})


@pytest.mark.parametrize("case_id,choice,count", [
    ("P02", "p02_choice", 2), ("P05", "p05_approved", 2), ("P06", "p06_choice", 3),
])
def test_alternative_routes_can_have_separate_endings(case_id, choice, count):
    ir = load_ir(case_id)
    branches = [flow for flow in ir.flows if flow.source == choice]
    ends = {node.id for node in ir.nodes if node.type == "endEvent"}
    assert len(branches) == len(ends) == count
    reached_ends = [reachable(ir, branch.target) & ends for branch in branches]
    assert all(len(reached) == 1 for reached in reached_ends)
    assert len(set.union(*reached_ends)) == count
    assert not [issue for issue in validate_graph(ir) if issue.severity == "error"]


def test_two_organizations_exchange_two_messages_only_between_pools():
    ir = load_ir("P10")
    assert {pool.name for pool in ir.participants} == {"Заказчик", "Поставщик"}
    nodes = {node.id: node for node in ir.nodes}
    messages = [flow for flow in ir.flows if flow.kind == "message"]
    assert len(messages) == 2
    for flow in messages:
        assert nodes[flow.source].process_id != nodes[flow.target].process_id
        assert nodes[flow.source].type == "sendTask"
        assert nodes[flow.target].type == "receiveTask"
    for flow in (flow for flow in ir.flows if flow.kind == "sequence"):
        assert nodes[flow.source].process_id == nodes[flow.target].process_id


def test_xml_special_characters_survive_parsing():
    root = etree.fromstring(serialize(load_ir("P13")).encode())
    names = root.xpath("//bpmn:task/@name", namespaces=NS)
    for literal in BY_ID["P13"]["expected"]["preserve_literals"] + ['Анализ "Север"']:
        assert any(literal in name for name in names)


def test_envelopes_request_missing_facts_without_fabricating_a_graph():
    envelopes = json.loads((ROOT / "examples/envelopes.json").read_text(encoding="utf-8"))
    assert set(envelopes) == {case["id"] for case in CASES if case["expected_status"] != "ready"}
    for case_id, envelope in envelopes.items():
        case = BY_ID[case_id]
        assert "python_code" not in envelope and "nodes" not in envelope
        if case["expected_status"] == "needs_clarification":
            assert envelope["mode"] == "clarify"
            assert envelope["questions"]
            for question in envelope["questions"]:
                assert question["question"]
                assert question["source_quote"] in case["text"]
        else:
            assert envelope["mode"] == "unsupported"
            assert envelope["reasons"]
            for feature in case["expected"]["unsupported_features"]:
                assert any(feature in reason for reason in envelope["reasons"])
