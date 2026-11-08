"""Compile only repository-owned synthetic fixture programs into review artifacts.

Run as ``uv run python -m scripts.compile_fixtures`` from the project root.
This offline developer command calls the bounded interpreter directly; it never
claims to test container isolation, an LLM provider, or a browser import.
The application runtime always uses the isolated worker boundary.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from backend.app.domain import PipelineError
from backend.app.graph import validate_graph
from backend.app.serialization import serialize
from backend.app.xml_validation import validate_xml
from worker.interpreter import interpret

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compile_fixture(case, output):
    """Preserve concrete results of every attempted known fixture, including errors."""
    started = perf_counter()
    directory = output / case["id"]
    directory.mkdir(parents=True, exist_ok=True)
    code = (ROOT / "examples/programs" / f"{case['id']}.py").read_text(encoding="utf-8")
    (directory / "source.txt").write_text(case["text"], encoding="utf-8")
    (directory / "generated.py").write_text(code, encoding="utf-8")
    checks = {key: "not_run" for key in ("code", "graph", "xsd", "layout", "import")}
    issues = []
    try:
        ir = interpret(code, case["text"])
        checks["code"] = "passed"
        write_json(directory / "process_ir.json", ir.model_dump())
        graph_issues = validate_graph(ir)
        issues.extend(issue.model_dump() for issue in graph_issues)
        checks["graph"] = "failed" if any(issue.severity == "error" for issue in graph_issues) else "passed"
        if checks["graph"] == "passed":
            xml = serialize(ir)
            (directory / "generated.bpmn").write_text(xml, encoding="utf-8")
            validation = validate_xml(xml)
            checks.update(validation["checks"])
            issues.extend(validation["issues"])
    except PipelineError as error:
        if checks["code"] == "not_run":
            checks["code"] = "failed"
        issues.append(error.issue.model_dump())
    passed = all(checks[key] == "passed" for key in ("code", "graph", "xsd", "layout"))
    report = {"checks": checks, "issues": issues}
    write_json(directory / "checks.json", report)
    write_json(directory / "metadata.json", {
        "case_id": case["id"],
        "mode": "fixture",
        "origin": "hand_authored_synthetic_fixture",
        "provider": None,
        "model": None,
        "attempt_count": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(perf_counter() - started, 6),
        "execution": "trusted_repository_fixture_direct_bounded_interpreter",
        "sandbox_check": "not_run",
        "semantic_check": "separate_tests/test_corpus.py",
        "llm_check": "not_run",
        "import_check": "not_run",
        "status": "passed" if passed else "failed",
    })
    return {"case_id": case["id"], "status": "passed" if passed else "failed", **report}


def main():
    cases = json.loads((ROOT / "examples/cases.json").read_text(encoding="utf-8"))["cases"]
    ready = [case for case in cases if case["expected_status"] == "ready"]
    parser = argparse.ArgumentParser(description="Проверка ручных синтетических BPMN-фикстур; без LLM.")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/m1")
    parser.add_argument("--case", choices=[case["id"] for case in ready], action="append")
    args = parser.parse_args()
    selected = [case for case in ready if args.case is None or case["id"] in args.case]
    reports = [compile_fixture(case, args.output) for case in selected]
    write_json(args.output / "summary.json", {
        "mode": "fixture",
        "note": "Ручные синтетические программы. Не проверяет LLM, изоляцию worker или импорт браузером.",
        "results": reports,
    })
    for report in reports:
        print(f"{report['case_id']}: {report['status']} (fixture; import=not_run)")
    return 0 if all(report["status"] == "passed" for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
