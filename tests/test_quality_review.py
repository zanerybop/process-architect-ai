"""Regression of identified meaning gaps and current-revision review; synthetic data only."""

import json

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from backend.app.builder import Builder
from backend.app.config import ROOT, Settings
from backend.app.domain import ProcessIR
from backend.app.llm import ProviderResult, parse_envelope
from backend.app.main import create_app
from backend.app.revision_review import review_revision
from backend.app.semantic_review import review_source
from backend.app.serialization import NS, serialize
from backend.app.service import GenerationService
from backend.app.storage import Storage
from tests.auth_helpers import authorize
from tests.test_api import TrustedFixtureRunner


def saved_ir(case):
    return ProcessIR.model_validate(
        json.loads(
            (ROOT / "tests/fixtures/quality" / f"{case}.ir.json").read_text(encoding="utf-8"),
        )
    )


def test_live_omitted_activity_detected_without_case_specific_id():
    ir = saved_ir("P05")
    issues = review_source(ir)["issues"]
    assert any(issue["code"] == "SOURCE_ACTION_OMITTED" for issue in issues)
    assert any("принимает решение" in issue["source_quote"] for issue in issues)


def test_source_quote_cannot_justify_opposite_action():
    builder = Builder("Аналитик отклоняет оплату.")
    builder.process(id="p", name="Проверка")
    builder.task(id="n", process_id="p", name="Одобрить оплату", source_quote="Аналитик отклоняет оплату.")
    assert review_source(builder.build())["issues"][0]["code"] == "SOURCE_ACTION_CONTRADICTION"


def test_compound_condition_retains_both_predicates_and_negation():
    ir = saved_ir("C01")
    assert any(issue["code"] == "SOURCE_CONDITION_INCOMPLETE" for issue in review_source(ir)["issues"])
    branch = next(flow for flow in ir.flows if flow.target == "end_reject_supplier")
    branch.name = "Бюджета достаточно и поставщик не одобрен"
    assert not any(issue["code"] == "SOURCE_CONDITION_INCOMPLETE" for issue in review_source(ir)["issues"])


def test_coverage_is_exact_and_not_claimed_complete_semantic_acceptance():
    ir = saved_ir("P10")
    report = review_source(ir)
    assert report["human_review_required"]
    assert report["coverage"]
    for item in report["coverage"]:
        assert ir.source_text[item["start"] : item["end"]] == item["text"]
    assert report["issues"] == []


def test_flow_provenance_has_real_offsets_and_rejects_invented_quote():
    builder = Builder("Если лимит достаточен, заказ принимается.")
    builder.flow(id="flow", source="s", target="t", source_quote="Если лимит достаточен")
    proof = builder.build().provenance[0]
    assert proof.element_id == "flow" and proof.source_start == 0
    with pytest.raises(Exception, match="Цитата связи"):
        builder.message_flow(id="other", source="s", target="t", source_quote="выдуманный текст")


def test_compound_condition_accepts_sequential_gates_only_when_first_is_mandatory():
    builder = Builder("Если бюджета достаточно, но поставщик отсутствует, процесс завершается.")
    builder.process(id="p", name="Проверка")
    builder.start(id="s", process_id="p")
    builder.xor_gateway(id="budget", process_id="p")
    builder.xor_gateway(id="vendor", process_id="p")
    builder.end(id="e", process_id="p")
    builder.flow(id="a", source="s", target="budget")
    builder.flow(id="b", source="budget", target="vendor", name="Бюджета достаточно")
    builder.flow(id="c", source="vendor", target="e", name="Поставщик отсутствует")
    assert not review_source(builder.build())["issues"]
    builder.flow(id="bypass", source="s", target="vendor")
    assert review_source(builder.build())["issues"][0]["code"] == "SOURCE_CONDITION_INCOMPLETE"


def test_explicit_named_data_must_survive_in_visible_diagram_not_only_quote():
    source = 'Аналитик открывает файл с названием «Свод & итог <2028>» и проверяет поле "Маржа > Ноль".'
    builder = Builder(source)
    builder.process(id="p", name="Проверка")
    builder.task(id="open", process_id="p", name="Открыть файл", source_quote=source)
    builder.task(id="check", process_id="p", name="Проверить поле", source_quote=source)
    ir = builder.build()
    issues = review_source(ir)["issues"]
    assert len(issues) == 2
    assert all(issue["code"] == "SOURCE_NAMED_DATA_OMITTED" for issue in issues)
    ir.nodes[0].name = "Открыть файл Свод & итог <2028>"
    ir.nodes[1].name = "Проверить поле Маржа > Ноль"
    assert not review_source(ir)["issues"]


def test_named_report_with_nested_quotes_and_non_data_quotes():
    source = 'Редактор сохраняет отчёт «Анализ "Юг"». Оператор говорит «готово».'
    builder = Builder(source)
    builder.process(id="p", name="Отчёт")
    builder.task(id="save", process_id="p", name='Сохранить Анализ "Юг"', source_quote=source)
    assert not review_source(builder.build())["issues"]


@pytest.mark.asyncio
async def test_all_invalid_quotes_reach_one_repair_with_exact_diagnostics(tmp_path):
    source = 'Инженер «Отдел А» получает прибор и регистрирует прибор.'
    code = '''bpmn.process(id="p", name="Проверка")
bpmn.lane(id="l", process_id="p", name="Инженер")
bpmn.start(id="s", process_id="p", lane_id="l")
bpmn.task(id="receive", process_id="p", lane_id="l", name="Получить прибор",
          source_quote="Инженер получает прибор")
bpmn.task(id="register", process_id="p", lane_id="l", name="Зарегистрировать прибор",
          source_quote="Инженер регистрирует прибор")
bpmn.end(id="e", process_id="p", lane_id="l")
bpmn.flow(id="a", source="s", target="receive")
bpmn.flow(id="b", source="receive", target="register")
bpmn.flow(id="c", source="register", target="e")'''
    corrected = code.replace("Инженер получает прибор", "получает прибор").replace(
        "Инженер регистрирует прибор", "регистрирует прибор",
    )
    provider = SequenceProvider([code, corrected])
    result = await GenerationService(
        Settings(_env_file=None), provider, TrustedFixtureRunner(), Storage(tmp_path),
    ).generate(source)
    assert result["status"] == "ready" and result["attempt_count"] == 2
    feedback = provider.calls[1]["feedback"][0]["issues"]
    assert len(feedback) == 2
    assert {item["source_quote"] for item in feedback} == {
        "Инженер получает прибор", "Инженер регистрирует прибор",
    }
    assert all(item["code"] == "SOURCE_QUOTE_NOT_FOUND" and item["suggested_action"] for item in feedback)


def test_geometry_keeps_review_valid_but_lane_name_and_type_changes_are_visible():
    ir = saved_ir("P10")
    original = {"ir": ir.model_dump()}
    root = etree.fromstring(serialize(ir).encode())
    bounds = root.find(".//dc:Bounds", NS)
    bounds.set("x", str(float(bounds.get("x")) + 10))
    report = review_revision(etree.tostring(root, encoding="unicode"), original)
    assert report["semantic_diff"]["status"] == "unchanged"
    task = root.find(".//bpmn:task", NS)
    task.set("name", "Исправленное название")
    report = review_revision(etree.tostring(root, encoding="unicode"), original)
    assert report["semantic_diff"]["status"] == "changed"
    assert any("name" in change["fields"] for change in report["semantic_diff"]["changes"])


def test_removed_action_is_explained_even_when_graph_is_reconnected():
    ir = saved_ir("P10")
    original = {"ir": ir.model_dump()}
    removed = next(node for node in ir.nodes if node.id == "register_offer")
    ir.nodes.remove(removed)
    ir.provenance = [item for item in ir.provenance if item.element_id != removed.id]
    incoming = next(flow for flow in ir.flows if flow.target == removed.id)
    outgoing = next(flow for flow in ir.flows if flow.source == removed.id)
    incoming.target = outgoing.target
    ir.flows.remove(outgoing)
    report = review_revision(serialize(ir), original)
    assert report["checks"]["graph"] == "passed"
    assert any(
        change["kind"] == "removed" and change["element_id"] == removed.id
        for change in report["semantic_diff"]["changes"]
    )


class SequenceProvider:
    provider, model = "test", "explicit-fixture"

    def __init__(self, codes):
        self.codes = iter(codes)
        self.calls = []

    async def generate(self, source, feedback=None):
        self.calls.append({"source": source, "feedback": feedback})
        return ProviderResult(
            parse_envelope(
                {
                    "mode": "build",
                    "python_code": next(self.codes),
                    "summary": "Синтетический тест",
                    "assumptions": [],
                }
            ),
            self.provider,
            self.model,
        )


@pytest.mark.asyncio
async def test_behavior_error_repaired_without_disabling_the_gate(tmp_path):
    source = "Юрист проверяет договор, финансист проверяет бюджет."
    code = """bpmn.process(id="p", name="Проверки")
bpmn.lane(id="l", process_id="p", name="Юрист")
bpmn.lane(id="r", process_id="p", name="Финансист")
bpmn.start(id="s", process_id="p", lane_id="l")
bpmn.xor_gateway(id="split", process_id="p", lane_id="l", name="Разделение")
bpmn.task(id="a", process_id="p", lane_id="l", name="Проверить договор",
          source_quote="Юрист проверяет договор")
bpmn.task(id="b", process_id="p", lane_id="r", name="Проверить бюджет",
          source_quote="финансист проверяет бюджет")
bpmn.parallel_gateway(id="join", process_id="p", lane_id="l")
bpmn.end(id="e", process_id="p", lane_id="l")
bpmn.flow(id="f1", source="s", target="split")
bpmn.flow(id="f2", source="split", target="a")
bpmn.flow(id="f3", source="split", target="b")
bpmn.flow(id="f4", source="a", target="join")
bpmn.flow(id="f5", source="b", target="join")
bpmn.flow(id="f6", source="join", target="e")"""
    provider = SequenceProvider(
        [code, code.replace('bpmn.xor_gateway(id="split"', 'bpmn.parallel_gateway(id="split"')]
    )
    storage = Storage(tmp_path)
    result = await GenerationService(
        Settings(_env_file=None), provider, TrustedFixtureRunner(), storage
    ).generate(source)
    assert result["status"] == "ready" and result["attempt_count"] == 2
    failure = provider.calls[1]["feedback"][0]["issues"][0]
    assert failure["code"].startswith("BEHAVIOR_") and failure["witness_path"]
    assert result["checks"]["behavior"] == "passed"


@pytest.mark.asyncio
async def test_meaning_error_repaired_and_all_attempts_recorded(tmp_path):
    source = "Аналитик отклоняет оплату."
    code = """bpmn.process(id="p", name="Оплата")
bpmn.lane(id="l", process_id="p", name="Аналитик")
bpmn.start(id="s", process_id="p", lane_id="l")
bpmn.task(id="t", process_id="p", lane_id="l", name="Одобрить оплату",
          source_quote="Аналитик отклоняет оплату.")
bpmn.end(id="e", process_id="p", lane_id="l")
bpmn.flow(id="a", source="s", target="t")
bpmn.flow(id="b", source="t", target="e")"""
    provider = SequenceProvider([code, code.replace("Одобрить оплату", "Отклонить оплату")])
    storage = Storage(tmp_path)
    service = GenerationService(Settings(_env_file=None), provider, TrustedFixtureRunner(), storage)
    result = await service.generate(source)
    assert result["status"] == "ready" and result["attempt_count"] == 2
    assert provider.calls[1]["feedback"][0]["issues"][0]["code"] == "SOURCE_ACTION_CONTRADICTION"
    manifest = json.loads(storage.artifact(result["id"], "metadata").read_text(encoding="utf-8"))
    assert manifest["prompt_sha256"] and manifest["prompt_version"]
    assert manifest["attempts"][0]["semantic_review"]["issues"]
    assert manifest["attempts"][0]["ir"] and manifest["attempts"][1]["ir"]


def test_review_is_owner_scoped_and_clarification_input_bounded(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), runner=TrustedFixtureRunner())
    with TestClient(app) as client:
        assert client.post("/api/review", json={"xml": "<xml/>"}).status_code == 401
        authorize(client)
        assert (
            client.post("/api/review", json={"xml": "<xml/>", "generation_id": "a" * 32}).status_code == 404
        )
        assert (
            client.post(
                "/api/generate",
                json={
                    "description": "Описание",
                    "clarification_answers": [
                        {"question": "Вопрос?", "answer": " ", "source_quote": "Описание"},
                    ],
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/generate",
                json={
                    "description": "Описание",
                    "clarification_answers": [
                        {"question": "Вопрос?", "answer": "Ответ", "source_quote": "Несуществующая цитата"},
                    ],
                },
            ).status_code
            == 422
        )
