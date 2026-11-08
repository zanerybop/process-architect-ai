"""Known synthetic examples shared by the UI and the explicit fixture provider."""

import json

from backend.app.config import ROOT


def load_examples() -> dict:
    document = json.loads((ROOT / "examples/cases.json").read_text(encoding="utf-8"))
    cases = list(document["cases"])
    for path in sorted((ROOT / "examples/full_bpmn").glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        cases.append({
            "id": manifest["id"], "title": manifest["title"], "kind": "positive",
            "expected_status": "ready", "origin": "authored_fixture_no_llm",
            "text": (path.parent / "source.txt").read_text(encoding="utf-8"),
            "features": manifest["features"],
        })
    return {**document, "cases": cases}


def fixture_program(identifier: str):
    if identifier.startswith("F"):
        return ROOT / "examples/full_bpmn" / identifier / "process.py"
    return ROOT / "examples/programs" / f"{identifier}.py"
