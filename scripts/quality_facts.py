"""Derive presentation facts from completed evidence; fail rather than invent missing metrics."""

import json
import math
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main():
    benchmark = read("artifacts/quality/benchmark/run-02/summary.json")
    assert benchmark["not_run"] == 0, "Final benchmark is incomplete"
    assert len(benchmark["configurations"]) == 1, "Do not combine model/prompt configurations"
    clean = read("artifacts/clean-install/report.json")
    assert clean["status"] == "PASS", "Clean installation checks are incomplete"
    junit = ElementTree.parse(ROOT / "artifacts/quality/final-python-tests.xml").getroot().find("testsuite")
    assert junit is not None and junit.get("failures") == junit.get("errors") == "0"
    browser = read("artifacts/quality/final-browser-tests.json")
    assert browser["stats"]["unexpected"] == browser["stats"]["skipped"] == browser["stats"]["flaky"] == 0
    live_browser = read("artifacts/quality/benchmark/browser-02/report.json")
    counts = Counter(row["actual_status"] for row in benchmark["runs"])
    # Only usable XML outputs are in browser acceptance, while all requests stay in model metrics.
    assert live_browser["total"] == counts["ready"], "Not all ready outputs have browser evidence"
    assert live_browser["status"] == "PASS"
    video = read("artifacts/quality/video/media-validation.json")
    assert video["status"] == "PASS" and video["duration_seconds"] <= 420
    configuration = benchmark["configurations"][0]
    properties = benchmark["critical_named_properties_positive"]
    ready_times = sorted(
        row["elapsed_seconds"] for row in benchmark["runs"] if row["actual_status"] == "ready"
    )
    facts = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": configuration["model"], "provider": configuration["provider"],
        "prompt_sha256": configuration["prompt_sha256"],
        "benchmark": {
            "cases": len({row["case_id"] for row in benchmark["runs"]}),
            "runs": benchmark["completed_runs"], "ready": counts["ready"],
            "clarified": counts["needs_clarification"], "unsupported": counts["unsupported"],
            "errors": counts["error"],
            "first_attempt_success": benchmark["first_attempt_candidate_success"]["numerator"],
            "candidate_success": benchmark["candidate_success"]["numerator"],
            "semantic_pass": properties["numerator"], "semantic_total": properties["denominator"],
            "p50_seconds": benchmark["timing_all_completed_including_failures"]["p50_seconds"],
            "p95_seconds": benchmark["timing_all_completed_including_failures"]["p95_seconds"],
            "ready_p50_seconds": statistics.median(ready_times) if ready_times else None,
            "ready_p95_seconds": ready_times[math.ceil(len(ready_times) * .95) - 1] if ready_times else None,
            "max_seconds": max(row["elapsed_seconds"] for row in benchmark["runs"]),
            "browser_import_pass": live_browser["passed"], "browser_import_total": live_browser["total"],
            "behavior_counts": benchmark["behavior_counts"], "repair_runs": benchmark["repair_runs"],
        },
        "tests": {"python_passed": int(junit.get("tests")), "browser_passed": browser["stats"]["expected"]},
        "scope_notes": [
            "Регрессионный корпус: 25 синтетических описаний × 3 повтора; все ошибки и попытки учтены.",
            "Примеры P13/Q07 из baseline использованы для улучшений; это не слепой тест новых процессов.",
            "Смысловые свойства проверены правилами; независимая человеческая приёмка не выполнена.",
            "Baseline run01 и видеогенерация используют предыдущую v2; финальный run02 — v3.",
        ],
        "limitations": [
            "Полноту смысла подтверждает аналитик; автоматические правила проверяют ограниченные признаки.",
            "Межпуловая доставка сообщений и истинность условий исполнения не доказываются.",
            "Яндекс AI Studio: профиль и тесты готовы, доступа для live-проверки пока нет.",
            "Экономия времени ещё не измерена сравнительным пилотом с аналитиками.",
        ],
        "benchmark_report": "artifacts/quality/benchmark/run-02/summary.json",
        "presentation_screenshot": "artifacts/quality/video/diagram.png",
        "video": video,
    }
    target = ROOT / "artifacts/quality/release-facts.json"
    target.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"file": str(target), "benchmark": facts["benchmark"], "tests": facts["tests"]}))


if __name__ == "__main__":
    main()
