"""Offline oracle/harness tests; HTTP is mocked and no generated Python is executed."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from scripts import quality_benchmark as benchmark
from tests.test_corpus import load_ir
from tests.test_xml import make_ir, parallel_ir

CASES = json.loads(Path("examples/quality_cases.json").read_text(encoding="utf-8"))["cases"]


def response(ir=None, **overrides):
    return {
        "id": "a" * 32,
        "status": "ready",
        "attempt_count": 1,
        "python_code": "# Opaque data: the harness must never execute this text.",
        "bpmn_xml": "<synthetic-test-response />",
        "ir": (ir or make_ir()).model_dump(),
        "checks": {
            "code": "passed",
            "graph": "passed",
            "xsd": "passed",
            "layout": "passed",
            "behavior": "passed",
            "import": "not_run",
        },
        "issues": [],
        **overrides,
    }


def test_corpus_is_25_cases_with_all_three_expected_statuses_and_independent_holdout():
    assert len(CASES) == len({case["id"] for case in CASES}) == 25
    assert {case["expected_status"] for case in CASES} == {"ready", "needs_clarification", "unsupported"}
    assert sum(case["expected_status"] == "ready" for case in CASES) == 18
    assert sum(case["origin"] == "new_synthetic_holdout_case_not_used_in_prompt" for case in CASES) == 10
    assert all(case["semantic_named_properties"] for case in CASES if case["expected_status"] == "ready")


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case["id"].startswith("P") and case["expected_status"] == "ready"],
    ids=lambda case: case["id"],
)
def test_existing_independent_annotations_match_trusted_handwritten_fixtures(case):
    checks = benchmark.evaluate_properties(case, response(load_ir(case["id"])))
    assert all(item["passed"] for item in checks), checks


def test_p05_missing_manager_action_does_not_pass_because_gateway_has_same_name():
    case = next(case for case in CASES if case["id"] == "P05")
    ir = load_ir("P05")
    decision = next(node for node in ir.nodes if node.name == "Принять решение")
    decision.type = "exclusiveGateway"
    checks = {item["id"]: item for item in benchmark.evaluate_properties(case, response(ir))}
    assert checks["decision"]["passed"] is False


def test_action_assigned_to_wrong_role_fails_named_property():
    ir = make_ir()
    ir.nodes[1].name = "Проверить договор"
    case = {
        "semantic_named_properties": [
            {"id": "legal", "kind": "action", "pattern": "провер.*договор", "role_pattern": "юрист"}
        ]
    }
    assert benchmark.evaluate_properties(case, response(ir))[0]["passed"] is False


def test_parallel_join_property_rejects_missing_synchronization():
    ir = parallel_ir()
    ir.nodes[2].name, ir.nodes[3].name = "Проверить договор", "Проверить бюджет"
    ir.nodes[5].type, ir.nodes[5].name = "task", "Принять решение"
    case = {
        "semantic_named_properties": [
            {"id": "both", "kind": "parallel_join", "branches": ["договор", "бюджет"], "after": "решени"}
        ]
    }
    assert benchmark.evaluate_properties(case, response(ir))[0]["passed"] is True
    ir.nodes[4].type = "exclusiveGateway"
    assert benchmark.evaluate_properties(case, response(ir))[0]["passed"] is False


def test_order_requires_no_bypass_and_condition_must_be_on_every_target_path():
    ir = make_ir(
        [
            ("start", "startEvent"),
            ("choice", "exclusiveGateway"),
            ("check", "task"),
            ("finish", "task"),
            ("end", "endEvent"),
        ],
        [("start", "choice"), ("choice", "check"), ("check", "finish"), ("finish", "end")],
    )
    ir.nodes[2].name, ir.nodes[3].name = "Проверить", "Заказать"
    ir.flows[1].name = "Бюджета достаточно"
    case = {
        "semantic_named_properties": [
            {"id": "before", "kind": "order", "before": "провер", "after": "заказ"},
            {"id": "funded", "kind": "condition_on_path", "target": "заказ", "pattern": "бюджет.*достаточ"},
        ]
    }
    assert all(item["passed"] for item in benchmark.evaluate_properties(case, response(ir)))
    ir.flows.append(ir.flows[0].model_copy(update={"id": "bypass", "source": "choice", "target": "finish"}))
    assert not any(item["passed"] for item in benchmark.evaluate_properties(case, response(ir)))


def test_inconclusive_behavior_and_expert_review_remain_visible():
    case = {"id": "QTEST", "expected_status": "ready", "semantic_named_properties": []}
    payload = response()
    payload["checks"]["behavior"] = "inconclusive"
    record = benchmark.evaluate_result(case, payload, 12)
    assert record["candidate_success"] is True
    assert record["behavior"] == "inconclusive"
    assert record["expert_semantic_review"] == "NOT_REVIEWED"
    payload["checks"]["behavior"] = "failed"
    assert benchmark.evaluate_result(case, payload, 12)["candidate_success"] is False


def test_expected_clarification_is_success_but_error_and_unrequested_clarification_are_not():
    case = {"id": "QA", "expected_status": "needs_clarification", "semantic_named_properties": []}
    clarify = benchmark.evaluate_result(case, {"status": "needs_clarification", "attempt_count": 1}, 2)
    assert clarify["candidate_success"] is True
    positive = case | {"expected_status": "ready"}
    error = benchmark.evaluate_result(positive, {"status": "error", "attempt_count": 2}, 120)
    unexpected = benchmark.evaluate_result(positive, {"status": "needs_clarification", "attempt_count": 1}, 3)
    summary = benchmark.summarize([clarify, error, unexpected], planned=4)
    assert summary["status_match"] == {"numerator": 1, "denominator": 3, "rate": 0.3333}
    assert summary["candidate_success"]["denominator"] == 3
    assert summary["not_run"] == 1
    assert summary["error_runs"] == 1
    assert summary["repair_runs"] == 1
    assert summary["timing_all_completed_including_failures"]["p95_seconds"] == 120


def test_missing_ir_does_not_pass_negative_business_properties():
    case = {
        "semantic_named_properties": [
            {"id": "no_payment", "kind": "forbidden_action", "pattern": "оплат"},
            {"id": "no_and", "kind": "count", "metric": "and", "max": 0},
            {"id": "no_cycle", "kind": "cycle", "expected": False},
        ]
    }
    assert not any(item["passed"] for item in benchmark.evaluate_properties(case, {"status": "error"}))


def test_irrelevant_clarifying_question_is_not_counted_as_candidate_success():
    case = {
        "id": "QA",
        "expected_status": "needs_clarification",
        "semantic_named_properties": [
            {"id": "question_about_rejection", "kind": "question", "pattern": "отказ|отклон"},
        ],
    }
    record = benchmark.evaluate_result(
        case,
        {
            "status": "needs_clarification",
            "questions": [
                {"question": "Какого цвета должна быть схема?"},
            ],
        },
        1,
    )
    assert record["status_match"] is True
    assert record["candidate_success"] is False


def test_budget_priority_oracle_does_not_mistake_insufficient_for_sufficient():
    case = next(case for case in CASES if case["id"] == "Q06")
    pattern = next(
        prop["pattern"]
        for prop in case["semantic_named_properties"]
        if prop["id"] == "budget_before_vendor_rejection"
    )
    assert benchmark._matches(pattern, "Бюджета достаточно, поставщик отсутствует")
    assert not benchmark._matches(pattern, "Бюджета недостаточно")
    assert not benchmark._matches(pattern, "Бюджета не достаточно")


def test_http_harness_preserves_all_responses_and_resumes_without_reissuing(monkeypatch, tmp_path):
    secret = "only-in-memory-password-please"
    calls = []
    case = {
        "id": "Q01",
        "text": "Синтетические тестовые данные.",
        "expected_status": "ready",
        "semantic_named_properties": [],
    }

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"mode": "live", "generation_available": True})
        if request.url.path == "/api/auth/login":
            assert json.loads(request.content)["password"] == secret
            return httpx.Response(200, json={"csrf_token": "test-csrf"})
        if request.url.path == "/api/generate":
            assert request.headers["X-CSRF-Token"] == "test-csrf"
            if calls.count("/api/generate") == 1:
                return httpx.Response(200, json=response())
            return httpx.Response(500, json={"status": "error", "issues": []})
        if request.url.path.endswith("/artifacts/metadata"):
            return httpx.Response(
                200,
                json={
                    "provider": "mock-transport",
                    "model": "offline-fixture",
                    "prompt_sha256": "a" * 64,
                    "attempts": [{"python_code": "pass"}],
                },
            )
        assert request.url.path == "/api/auth/logout"
        return httpx.Response(204)

    original = httpx.Client
    monkeypatch.setattr(
        benchmark.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(benchmark, "provision_local_account", lambda _url: ("fixture-user", secret))
    first = benchmark.run_benchmark([case], 2, tmp_path, "http://localhost:8088", min_interval=0)
    assert first["completed_runs"] == 2
    assert first["candidate_success"]["numerator"] == 1
    before = len(calls)
    assert benchmark.run_benchmark([case], 2, tmp_path, "http://localhost:8088") == first
    assert len(calls) == before
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8")
            assert "test-csrf" not in path.read_text(encoding="utf-8")
    assert (tmp_path / "Q01-1/generated.py").is_file()
    assert (tmp_path / "Q01-1/process_ir.json").is_file()
    assert (tmp_path / "Q01-1/metadata.json").is_file()
    assert json.loads((tmp_path / "Q01-2/response.json").read_text())["status"] == "error"


def test_resume_rejects_tampered_completed_evidence(tmp_path):
    case = {"id": "Q01", "text": "Example"}
    artifact = tmp_path / "evaluation.json"
    artifact.write_text('{"status":"error"}', encoding="utf-8")
    marker = {
        "case_fingerprint": benchmark.case_fingerprint(case),
        "repeat": 1,
        "files": {"evaluation.json": hashlib.sha256(artifact.read_bytes()).hexdigest()},
    }
    (tmp_path / "complete.json").write_text(json.dumps(marker), encoding="utf-8")
    artifact.write_text('{"status":"ready"}', encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        benchmark.load_complete_run(tmp_path, case, 1)


def test_provisioning_uses_stdin_and_never_password_argument(monkeypatch):
    def fake_run(command, **kwargs):
        assert command[-1] == "--password-stdin"
        assert len(kwargs["input"].strip()) >= 20
        assert kwargs["input"].strip() not in command
        return subprocess_result()

    def subprocess_result():
        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(benchmark.subprocess, "run", fake_run)
    username, password = benchmark.provision_local_account("http://127.0.0.1:8088")
    assert username.startswith("bench_") and len(password) >= 20
    with pytest.raises(ValueError):
        benchmark.provision_local_account("https://remote.example")


def test_stop_marker_keeps_planned_denominator_and_prevents_network(monkeypatch, tmp_path):
    case = {"id": "Q01", "text": "Синтетический процесс", "expected_status": "ready"}
    (tmp_path / "STOP").write_text("operator requested diagnostic cutoff", encoding="utf-8")

    def forbidden(**kwargs):
        pytest.fail("No request is permitted after STOP marker")

    monkeypatch.setattr(benchmark.httpx, "Client", forbidden)
    report = benchmark.run_benchmark([case], 3, tmp_path, "http://127.0.0.1:8000")
    assert report["planned_runs"] == report["not_run"] == 3
    assert report["completed_runs"] == 0
    assert "STOP marker" in report["stop_reason"]


def test_stop_during_request_saves_result_metadata_and_logs_out_before_next_request(monkeypatch, tmp_path):
    calls = []
    case = {"id": "Q01", "text": "Синтетический процесс", "expected_status": "ready"}

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"mode": "live", "generation_available": True})
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"csrf_token": "synthetic-csrf"})
        if request.url.path == "/api/generate":
            (tmp_path / "STOP").write_text("stop requested while processing", encoding="utf-8")
            return httpx.Response(200, json=response())
        if request.url.path.endswith("/artifacts/metadata"):
            return httpx.Response(200, json={"provider": "offline-test", "attempts": []})
        assert request.url.path == "/api/auth/logout"
        return httpx.Response(204)

    original = httpx.Client
    monkeypatch.setattr(
        benchmark.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(benchmark, "provision_local_account", lambda _url: ("fixture", "fixture-password"))
    report = benchmark.run_benchmark([case], 3, tmp_path, "http://127.0.0.1:8000", min_interval=0)
    assert report["completed_runs"] == 1 and report["not_run"] == 2
    assert calls.count("/api/generate") == calls.count("/api/auth/logout") == 1
    assert (tmp_path / "Q01-1/complete.json").is_file()
    assert (tmp_path / "Q01-1/metadata.json").is_file()
    assert not (tmp_path / "Q01-2/source.txt").exists()
