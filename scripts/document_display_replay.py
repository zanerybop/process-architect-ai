"""Replay every ready model program through the real worker after display fixes.

No LLM requests and no host execution of generated Python. The baseline is read-only.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.container_check import docker

ROOT = Path(__file__).resolve().parents[1]
PROBE = '''import asyncio, json
from backend.app.runner import ContainerRunner
from backend.app.serialization import serialize
from backend.app.xml_validation import validate_xml

async def main():
    ir = await ContainerRunner().run(payload["code"], payload["source"])
    xml = serialize(ir)
    print(json.dumps({"ir":ir.model_dump(mode="json"), "xml":xml,
                      "validation":validate_xml(xml)}, ensure_ascii=True))

asyncio.run(main())
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def allowed_ir_changes(original, rebuilt):
    """Only missing display names may become the exact existing expression text."""
    before = {item["key"]: item for item in original["document"]["elements"]}
    after = {item["key"]: item for item in rebuilt["document"]["elements"]}
    assert before.keys() == after.keys(), "Element set changed"
    labels = []
    for key, left in before.items():
        right = after[key]
        if left == right:
            continue
        assert left["tag"] in {"bpmn:sequenceFlow", "bpmn:flowElement"}
        assert not left["attributes"].get("name", "").strip()
        conditions = [
            item for item in before.values()
            if item["parent"] == key and item["tag"] == "bpmn:conditionExpression"
        ]
        assert len(conditions) == 1 and right["attributes"]["name"] == conditions[0]["text"]
        expected = {**left, "attributes": {**left["attributes"], "name": conditions[0]["text"]}}
        assert expected == right, "Unexpected semantic change"
        labels.append({"flow": key, "name": right["attributes"]["name"]})
    outside_document = {key: value for key, value in original.items() if key != "document"}
    assert outside_document == {key: value for key, value in rebuilt.items() if key != "document"}
    assert original["document"]["namespaces"] == rebuilt["document"]["namespaces"]
    return labels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark", default="artifacts/full-bpmn/benchmark/20261004T-current-document"
    )
    parser.add_argument("--output", default="artifacts/full-bpmn/display-replay")
    args = parser.parse_args()
    source, output = ROOT / args.benchmark, ROOT / args.output
    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    assert summary["not_run"] == 0 and summary["source_unchanged_at_end"]
    assert not output.exists(), "Use a new output directory to preserve previous evidence"
    output.mkdir(parents=True)
    rows = []
    for run in summary["runs"]:
        if run["actual_status"] != "ready":
            continue
        name = run["artifact_directory"]
        baseline = source / name
        target = output / name
        target.mkdir()
        payload = {
            "code": (baseline / "generated.py").read_text(encoding="utf-8"),
            "source": (baseline / "source.txt").read_text(encoding="utf-8"),
        }
        row = {"id": name, "baseline": baseline.relative_to(ROOT).as_posix(), "status": "FAIL"}
        rows.append(row)
        try:
            probe = "payload = " + repr(payload) + "\n" + PROBE
            result = json.loads(docker(
                "compose", "exec", "-T", "api", "python", "-", stdin=probe, timeout=45,
            ))
            original = json.loads((baseline / "process_ir.json").read_text(encoding="utf-8"))
            row["labels_added"] = allowed_ir_changes(original, result["ir"])
            for key in ("graph", "xsd", "layout"):
                assert result["validation"]["checks"][key] == "passed", result["validation"]
            (target / "generated.bpmn").write_text(result["xml"], encoding="utf-8")
            (target / "generated.py").write_text(payload["code"], encoding="utf-8")
            (target / "source.txt").write_text(payload["source"], encoding="utf-8")
            (target / "process_ir.json").write_text(
                json.dumps(result["ir"], ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (target / "validation.json").write_text(
                json.dumps(result["validation"], ensure_ascii=False, indent=2), encoding="utf-8"
            )
            row.update({
                "status": "PASS", "checks": result["validation"]["checks"],
                "python_sha256": digest(target / "generated.py"),
                "baseline_xml_sha256": digest(baseline / "generated.bpmn"),
                "rebuilt_xml_sha256": digest(target / "generated.bpmn"),
            })
            assert row["python_sha256"] == digest(baseline / "generated.py")
        except (RuntimeError, ValueError, AssertionError, KeyError) as error:
            row["status"] = "FAIL"
            row["error"] = str(error)
        (target / "complete.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"id": name, "status": row["status"]}), flush=True)
    report = {
        "created_at": datetime.now(UTC).isoformat(), "model_requests": 0,
        "execution": "API ContainerRunner -> private isolated worker",
        "baseline": args.benchmark,
        "compiler_sources": {
            name: digest(ROOT / name) for name in (
                "backend/app/document_builder.py", "backend/app/document_serialization.py",
                "worker/interpreter.py",
            )
        },
        "total": len(rows), "passed": sum(row["status"] == "PASS" for row in rows),
        "cases": rows,
    }
    report["status"] = "PASS" if report["passed"] == report["total"] else "FAIL"
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
