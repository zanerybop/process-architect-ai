"""Release evidence contracts; these synthetic counts are never release measurements."""

import json

import pytest

from scripts.document_release_facts import browser_facts, build_facts, evidence_hash, python_count


def write_json(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def evidence(tmp_path):
    config = {"model": "test-model", "prompt_version": "test-prompt", "prompt_sha256": "abc"}
    runs = [
        dict(config, id="S1", title="Synthetic process", artifact_directory="S1-1",
             actual_status="ready", expected_status="ready", attempt_count=2, candidate_success=True),
        dict(config, id="S2", title="Synthetic clarification", artifact_directory="S2-1",
             actual_status="needs_clarification", expected_status="needs_clarification",
             attempt_count=1, candidate_success=True),
    ]
    for run in runs:
        write_json(tmp_path, f"benchmark/{run['artifact_directory']}/metadata.json",
                   dict(config, created_at="2030-02-03T12:00:00+00:00"))
    def fraction(n, d):
        return {"numerator": n, "denominator": d}
    summary = {
        "not_run": 0, "planned_runs": 2, "completed_runs": 2, "source_unchanged_at_end": True,
        "runs": runs, "timing_ready_only": {"p50_seconds": 2.5, "p95_seconds": 2.5},
        "ready_positive": fraction(1, 1), "candidate_success": fraction(2, 2),
        "first_attempt_candidate_success": fraction(1, 2),
        "critical_named_properties_positive": fraction(1, 1), "clarification_correct": fraction(1, 1),
        "repair_runs": 1, "repair_attempts": 1, "behavior_counts": {"inconclusive": 1},
        "limitations": ["Synthetic contract fixture"],
    }
    write_json(tmp_path, "benchmark/summary.json", summary)
    (tmp_path / "python.xml").write_text(
        '<testsuites><testsuite tests="4" failures="0" errors="0" skipped="1"/></testsuites>',
        encoding="utf-8",
    )
    xml = "benchmark/S1-1/generated.bpmn"
    (tmp_path / xml).write_text("<definitions/>", encoding="utf-8")
    browser = {
        "status": "PASS", "cases": [{
            "origin": "current_benchmark_live_output", "input": xml,
            "input_sha256": evidence_hash(xml, tmp_path), "status": "PASS",
            "input_unchanged": True, "semantic_preservation": {"preserved": True},
            "console_errors": [], "page_errors": [],
        }],
    }
    write_json(tmp_path, "browser.json", browser)
    write_json(tmp_path, "worker.json", {"status": "PASS", "checks": {"first": True, "second": True}})
    example = {"manifest_path": "manifest.json", "source_path": "source.txt", "svg_path": "example.svg"}
    for path in example.values():
        (tmp_path / path).write_text("example", encoding="utf-8")
    write_json(tmp_path, "example.json", example)
    arguments = {
        "benchmark": "benchmark", "example_path": "example.json", "python_report": "python.xml",
        "browser_report": "browser.json", "worker_report": "worker.json", "root": tmp_path,
    }
    return arguments, summary, browser


def test_builds_only_selected_evidence_and_keeps_denominators_distinct(evidence):
    args, _, _ = evidence
    facts = build_facts(**args)
    assert facts["prompt_version"] == "test-prompt"
    assert facts["model"] == "test-model"
    assert facts["date"] == "03.02.2030"
    assert facts["tests"]["python_passed"] == 3
    assert facts["tests"]["browser_passed"] == 1
    assert facts["tests"]["browser_kind"] == "diagram_roundtrips"
    assert facts["tests"]["browser_label"] == "1 схема в браузере"
    assert facts["tests"]["worker_report"] == "worker.json"
    assert facts["benchmark"]["first_attempt_ready"] == 0
    assert facts["benchmark"]["rows"][2][1] == "1 / 2"
    assert facts["benchmark"]["source_label"].startswith("2 пользовательских запроса · 3 вызова")
    assert len(facts["metadata_sha256"]) == 2
    assert set(facts["evidence_sha256"]) == {
        "benchmark/summary.json", "python.xml", "browser.json", "worker.json",
    }


@pytest.mark.parametrize("update", [
    {"not_run": 1}, {"completed_runs": 1}, {"source_unchanged_at_end": False},
])
def test_rejects_partial_or_changed_series(evidence, update):
    args, summary, _ = evidence
    summary.update(update)
    write_json(args["root"], "benchmark/summary.json", summary)
    with pytest.raises(AssertionError):
        build_facts(**args)


def test_rejects_summary_metadata_disagreement(evidence):
    args, summary, _ = evidence
    summary["runs"][0]["prompt_version"] = "different"
    write_json(args["root"], "benchmark/summary.json", summary)
    with pytest.raises(AssertionError, match="metadata disagree"):
        build_facts(**args)


def test_rejects_mixed_prompt_versions(evidence):
    args, summary, _ = evidence
    summary["runs"][0]["prompt_version"] = "different"
    write_json(args["root"], "benchmark/summary.json", summary)
    path = "benchmark/S1-1/metadata.json"
    metadata = json.loads((args["root"] / path).read_text())
    metadata["prompt_version"] = "different"
    write_json(args["root"], path, metadata)
    with pytest.raises(AssertionError, match="different models/prompts"):
        build_facts(**args)


@pytest.mark.parametrize("change", [
    "missing_ready", "duplicate", "changed_xml", "lost_semantics", "page_error",
])
def test_rejects_incomplete_or_invalid_browser_evidence(evidence, change):
    args, _, browser = evidence
    case = browser["cases"][0]
    if change == "missing_ready":
        case["origin"] = "fixture"
    elif change == "duplicate":
        browser["cases"].append(case.copy())
    elif change == "changed_xml":
        (args["root"] / case["input"]).write_text("changed", encoding="utf-8")
    elif change == "lost_semantics":
        case["semantic_preservation"]["preserved"] = False
    else:
        case["page_errors"] = ["failure"]
    write_json(args["root"], "browser.json", browser)
    with pytest.raises(AssertionError):
        build_facts(**args)


def test_preserves_playwright_regression_report_support(evidence):
    args, summary, _ = evidence
    write_json(args["root"], "browser.json", {
        "stats": {"expected": 6, "unexpected": 0, "skipped": 0, "flaky": 0},
    })
    assert browser_facts("browser.json", summary["runs"], args["root"]) == (
        6, "regression_tests", "6 браузерных тестов",
    )


def test_production_app_report_checks_reimport_and_keeps_its_scope(evidence):
    args, _, browser = evidence
    case = browser["cases"][0]
    case.update(renderer="production_document", reimport="PASS", edit={"name_changed": True})
    browser.update(total=1, passed=1)
    write_json(args["root"], "browser.json", browser)
    facts = build_facts(**args)
    assert facts["tests"]["browser_kind"] == "production_diagram_roundtrips"
    assert facts["tests"]["browser_label"] == "1 схема в приложении"
    case["reimport"] = "FAIL"
    write_json(args["root"], "browser.json", browser)
    with pytest.raises(AssertionError, match="incomplete"):
        build_facts(**args)


def test_junit_root_variants_and_failed_reports(tmp_path):
    (tmp_path / "root.xml").write_text('<testsuite tests="2" failures="0" errors="0"/>')
    assert python_count("root.xml", tmp_path) == 2
    (tmp_path / "root.xml").write_text('<testsuite tests="2" failures="1" errors="0"/>')
    with pytest.raises(AssertionError, match="failures"):
        python_count("root.xml", tmp_path)
