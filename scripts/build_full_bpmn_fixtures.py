"""Compile explicitly authored fixtures through the bounded Python interpreter.

No model or API request is made. These files prove deterministic support, not LLM
generation quality. Every example retains its source, Python, document IR and XML.
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from backend.app.config import Settings
from backend.app.document_serialization import serialize_document
from backend.app.service import GenerationService
from backend.app.storage import Storage
from backend.app.xml_validation import validate_xml
from worker.interpreter import interpret

ROOT = Path(__file__).resolve().parents[1]


class AuthoredFixtureProvider:
    """Return the authored program; no model transport or generation claim."""

    provider, model = "authored_fixture", "none"

    def __init__(self, code):
        self.code = code

    async def generate(self, description, feedback=None):
        return SimpleNamespace(
            envelope=SimpleNamespace(
                mode="build", python_code=self.code, assumptions=[], summary="Авторский пример BPMN"
            ),
            usage=None,
        )


class TrustedFixtureRunner:
    """In-process test double. This check does not prove worker isolation."""

    async def run(self, code, source_text):
        return interpret(code, source_text)


async def check_service(source, code, output):
    data_dir = output / "service"
    service = GenerationService(
        Settings(_env_file=None, app_mode="mock", data_dir=data_dir, llm_max_repair_attempts=0),
        AuthoredFixtureProvider(code),
        TrustedFixtureRunner(),
        Storage(data_dir),
    )
    result = await service.generate(source)
    (output / "pipeline-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "status": result["status"],
        "checks": result["checks"],
        "issues": result["issues"],
        "semantic_review": result["semantic_review"],
        "artifact_id": result["id"],
        "model_requests": 0,
        "runner": "trusted_in_process_test_double_not_isolation_evidence",
    }


def main():
    results = []
    for directory in sorted((ROOT / "examples/full_bpmn").iterdir()):
        if not directory.is_dir() or not (directory / "process.py").exists():
            continue
        source = (directory / "source.txt").read_text(encoding="utf-8")
        code = (directory / "process.py").read_text(encoding="utf-8")
        output = ROOT / "artifacts/full-bpmn/fixtures" / directory.name
        output.mkdir(parents=True, exist_ok=True)
        (output / "source.txt").write_text(source, encoding="utf-8")
        (output / "generated.py").write_text(code, encoding="utf-8")
        try:
            ir = interpret(code, source)
            xml = serialize_document(ir)
            checks = validate_xml(xml)
            (output / "process_ir.json").write_text(ir.model_dump_json(indent=2), encoding="utf-8")
            (output / "generated.bpmn").write_text(xml, encoding="utf-8")
            (output / "checks.json").write_text(
                json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            pipeline = asyncio.run(check_service(source, code, output))
            passed = (
                all(checks["checks"][name] == "passed" for name in ("xsd", "graph", "layout"))
                and pipeline["status"] == "ready"
            )
            result = {
                "case_id": directory.name,
                "status": "PASS" if passed else "FAIL",
                "checks": checks["checks"],
                "issues": checks["issues"],
                "xml_sha256": hashlib.sha256(xml.encode()).hexdigest(),
                "pipeline": pipeline,
            }
        except Exception as error:
            result = {
                "case_id": directory.name,
                "status": "FAIL",
                "error": str(error),
                "error_code": getattr(getattr(error, "issue", None), "code", None),
            }
        result.update(
            {
                "origin": "authored_fixture_no_llm",
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "python_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "model_requests": 0,
            }
        )
        (output / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "origin": "authored_fixture_no_llm",
        "status": "PASS" if results and all(item["status"] == "PASS" for item in results) else "FAIL",
        "total": len(results),
        "passed": sum(item["status"] == "PASS" for item in results),
        "cases": results,
    }
    (ROOT / "artifacts/full-bpmn/fixtures/report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
