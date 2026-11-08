"""Build presentation facts from explicitly selected, complete release evidence."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
BROWSER_ORIGINS = {"saved_model_output_recompiled", "current_benchmark_live_output"}


def workspace_path(name, root=ROOT):
    path = (root / name).resolve()
    assert path.is_relative_to(root.resolve()), "Evidence must stay inside the workspace"
    return path


def read(name, root=ROOT):
    return json.loads(workspace_path(name, root).read_text(encoding="utf-8"))


def evidence_hash(name, root=ROOT):
    return hashlib.sha256(workspace_path(name, root).read_bytes()).hexdigest()


def ratio(value):
    return f"{value['numerator']} / {value['denominator']}"


def seconds(value):
    return "нет готовых схем" if value is None else f"{value:.1f}".replace(".", ",")


def counted(value, one, few, many):
    if 11 <= value % 100 <= 14:
        form = many
    else:
        form = one if value % 10 == 1 else few if 2 <= value % 10 <= 4 else many
    return f"{value} {form}"


def python_count(name, root=ROOT):
    report = ElementTree.parse(workspace_path(name, root)).getroot()
    suites = [item for item in report.iter("testsuite") if not item.findall("testsuite")]
    assert suites, "No JUnit test suites"
    assert all(int(item.get(key, "0")) == 0 for item in suites for key in ("failures", "errors")), (
        "The Python report contains failures"
    )
    return sum(int(item.get("tests", "0")) - int(item.get("skipped", "0")) for item in suites)


def browser_facts(name, runs, root=ROOT):
    report = read(name, root)
    if "stats" in report:
        stats = report["stats"]
        assert all(stats[key] == 0 for key in ("unexpected", "skipped", "flaky")), (
            "The browser regression report is not completely green"
        )
        return stats["expected"], "regression_tests", f"{stats['expected']} браузерных тестов"
    assert report["status"] == "PASS" and report["cases"], "Browser roundtrip failed or empty"
    assert all(case["status"] == "PASS" for case in report["cases"]), "Browser case failed"
    assert report.get("total", len(report["cases"])) == len(report["cases"]), "Browser count mismatch"
    assert report.get("passed", len(report["cases"])) == len(report["cases"]), "Browser passed count mismatch"
    reviewed = [case for case in report["cases"] if case["origin"] in BROWSER_ORIGINS]
    ready_ids = {run["artifact_directory"] for run in runs if run["actual_status"] == "ready"}
    reviewed_ids = [Path(case["input"]).parent.name for case in reviewed]
    assert set(reviewed_ids) == ready_ids and len(reviewed_ids) == len(ready_ids), (
        "Every ready benchmark output needs exactly one browser roundtrip"
    )
    for case in reviewed:
        assert evidence_hash(case["input"], root) == case["input_sha256"], "Browser input changed"
        assert case["input_unchanged"] and case["semantic_preservation"]["preserved"], (
            "Browser roundtrip did not preserve the source semantics"
        )
        assert not case["console_errors"] and not case["page_errors"], "Browser errors recorded"
    count = len(reviewed)
    if all(case.get("renderer") in {"production_modeler", "production_document"} for case in reviewed):
        assert all(case.get("reimport") == "PASS" and case.get("edit") for case in reviewed), (
            "Production App import/edit/export/reimport is incomplete"
        )
        label = counted(count, "схема", "схемы", "схем") + " в приложении"
        return count, "production_diagram_roundtrips", label
    return count, "diagram_roundtrips", counted(count, "схема", "схемы", "схем") + " в браузере"


def model_configuration(benchmark, runs, root=ROOT):
    configurations = set()
    dates = []
    metadata_hashes = {}
    for run in runs:
        name = str(Path(benchmark) / run["artifact_directory"] / "metadata.json")
        metadata = read(name, root)
        config = tuple(metadata[key] for key in ("model", "prompt_version", "prompt_sha256"))
        assert all(config), "Missing saved model/prompt metadata"
        assert config == tuple(run[key] for key in ("model", "prompt_version", "prompt_sha256")), (
            "Benchmark row and saved model metadata disagree"
        )
        configurations.add(config)
        dates.append(datetime.fromisoformat(metadata["created_at"]))
        metadata_hashes[Path(name).as_posix()] = evidence_hash(name, root)
    assert len(configurations) == 1, "Do not combine different models/prompts"
    return next(iter(configurations)), max(dates).strftime("%d.%m.%Y"), metadata_hashes


def build_facts(*, benchmark, example_path, python_report, browser_report, worker_report, root=ROOT):
    report_path = f"{benchmark}/summary.json"
    summary = read(report_path, root)
    assert summary["not_run"] == 0 and summary["completed_runs"] == summary["planned_runs"], (
        "Benchmark must be complete; do not publish partial measurements"
    )
    assert summary.get("source_unchanged_at_end") is True, "Benchmark source changed or is not frozen"
    runs = summary["runs"]
    assert runs and len(runs) == summary["completed_runs"], "Benchmark run count mismatch"
    (model, prompt, prompt_hash), date, metadata_hashes = model_configuration(benchmark, runs, root)
    counts = Counter(run["actual_status"] for run in runs)
    expected = Counter(run["expected_status"] for run in runs)
    repeats = Counter(run["id"] for run in runs)
    repeat_counts = set(repeats.values())
    scope = counted(len(repeats), "описание", "описания", "описаний")
    scope += (
        " × " + counted(next(iter(repeat_counts)), "запуск", "запуска", "запусков")
        if len(repeat_counts) == 1 else ", " + counted(len(runs), "запрос", "запроса", "запросов")
    )
    scope += (
        ": " + counted(expected["ready"], "построение", "построения", "построений") + " и "
        + counted(expected["needs_clarification"], "запрос", "запроса", "запросов") + " с нехваткой данных."
    )
    passed_python = python_count(python_report, root)
    passed_browser, browser_kind, browser_label = browser_facts(browser_report, runs, root)
    worker = read(worker_report, root)
    assert worker["status"] == "PASS" and worker["checks"] and all(
        value is True for value in worker["checks"].values()
    ), "Worker checks failed"
    example = read(example_path, root)
    for name in ("manifest_path", "source_path", "svg_path"):
        assert workspace_path(example[name], root).is_file(), f"Missing example evidence: {name}"
    timing = summary["timing_ready_only"]
    properties = summary["critical_named_properties_positive"]
    failed_cases = {
        run["id"]: run["title"] for run in runs
        if run["expected_status"] == "ready" and not run["candidate_success"]
    }
    failure_note = (
        "Неуспешные сценарии: " + "; ".join(title for _, title in sorted(failed_cases.items())) + ". "
        if failed_cases else "Все заданные свойства корпуса прошли проверку. "
    )
    paths = [report_path, python_report, browser_report, worker_report]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "date": date,
        "model": model,
        "prompt_version": prompt,
        "prompt_sha256": prompt_hash,
        "evidence_sha256": {name: evidence_hash(name, root) for name in paths},
        "metadata_sha256": metadata_hashes,
        "tests": {
            "python_passed": passed_python,
            "python_report": python_report,
            "python_label": counted(passed_python, "тест", "теста", "тестов") + " Python",
            "browser_passed": passed_browser,
            "browser_report": browser_report,
            "browser_kind": browser_kind,
            "browser_label": browser_label,
            "worker_passed": len(worker["checks"]),
            "worker_report": worker_report,
            "worker_label": counted(len(worker["checks"]), "проверка", "проверки", "проверок") + " worker",
            "note": "Python regression and browser evidence have separate scopes; counts are not added.",
        },
        "benchmark": {
            "report_path": report_path,
            "cases": len(repeats),
            "runs": len(runs),
            "ready": counts["ready"],
            "clarified": counts["needs_clarification"],
            "unsupported": counts["unsupported"],
            "errors": counts["error"],
            "first_attempt_ready": sum(
                run["actual_status"] == "ready" and run["attempt_count"] == 1 for run in runs
            ),
            "ready_p50_seconds": timing["p50_seconds"],
            "ready_p95_seconds": timing["p95_seconds"],
            "semantic_pass": properties["numerator"],
            "semantic_total": properties["denominator"],
            "repair_runs": summary["repair_runs"],
            "repair_attempts": summary["repair_attempts"],
            "behavior_counts": summary["behavior_counts"],
            "kicker": "Проверка на текущей модели",
            "scope": scope,
            "method": (
                "Авторский набор: обычные процессы, вложенность, компенсации, данные, "
                "хореография и разговоры. Условия проверки заданы до запуска; "
                "все ошибки и повторные попытки сохранены."
            ),
            "limitation": failure_note + "Полный бизнес-смысл требует проверки аналитиком.",
            "source_label": (
                counted(
                    len(runs), "пользовательский запрос", "пользовательских запроса",
                    "пользовательских запросов",
                )
                + " · " + counted(sum(run["attempt_count"] for run in runs), "вызов", "вызова", "вызовов")
                + f" модели с исправлениями · {date}"
            ),
            "rows": [
                [f"Готовая схема из {expected['ready']} построений", ratio(summary["ready_positive"])],
                ["Ожидаемый статус и свойства", ratio(summary["candidate_success"])],
                ["Верный итог с первой попытки", ratio(summary["first_attempt_candidate_success"])],
                ["Заданные смысловые свойства", ratio(properties)],
                ["Корректное уточнение", ratio(summary["clarification_correct"])],
                [
                    "Медиана / p95 готовых схем",
                    f"{seconds(timing['p50_seconds'])} / {seconds(timing['p95_seconds'])} с",
                ],
            ],
        },
        "example": example,
        "limitations": summary["limitations"],
        "evidence_notes": [
            f"Model and prompt {prompt} were read from every saved run metadata file.",
            "Earlier reports are retained as historical evidence, not combined with this measurement.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", required=True, help="Directory containing completed summary and runs")
    parser.add_argument("--example", required=True, help="JSON with verified example paths and caption")
    parser.add_argument("--python-report", required=True, help="Final JUnit XML")
    parser.add_argument(
        "--browser-report", required=True, help="Playwright regression or diagram roundtrip JSON",
    )
    parser.add_argument("--worker-report", required=True, help="Actual isolated worker check JSON")
    parser.add_argument("--output", default="artifacts/quality/presentation-v3/facts.json")
    args = parser.parse_args()
    facts = build_facts(
        benchmark=args.benchmark, example_path=args.example, python_report=args.python_report,
        browser_report=args.browser_report, worker_report=args.worker_report,
    )
    output = workspace_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"facts": str(output), "runs": facts["benchmark"]["runs"]}))


if __name__ == "__main__":
    main()
