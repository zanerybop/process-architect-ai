"""Trusted acceptance client, fed to API container stdin by container_check.

All fixture programs go through the production ContainerRunner and real worker.
This script never interprets the submitted Python in the API process.
"""

import asyncio
import json

import httpx

from backend.app.domain import PipelineError
from backend.app.runner import ContainerRunner
from backend.app.serialization import serialize
from backend.app.xml_validation import validate_xml

PROGRAM = '''bpmn.process(id="probe", name="Проверка worker")
bpmn.start(id="start", process_id="probe")
bpmn.task(id="task", process_id="probe", name="Первое имя")
bpmn.end(id="end", process_id="probe")
bpmn.flow(id="flow1", source="start", target="task")
bpmn.flow(id="flow2", source="task", target="end")'''


async def main():
    runner = ContainerRunner()
    checks = {}
    original = await runner.run(PROGRAM, "Синтетическая проверка исполнения Python.")
    changed = await runner.run(PROGRAM.replace("Первое имя", "Изменённое имя"), "")
    checks["python_controls_ir"] = (
        next(node.name for node in original.nodes if node.id == "task") == "Первое имя"
        and next(node.name for node in changed.nodes if node.id == "task") == "Изменённое имя"
    )
    validation = validate_xml(serialize(original))
    checks["worker_ir_to_xml_di"] = all(
        validation["checks"][name] == "passed" for name in ("xsd", "graph", "layout")
    )
    negative = {
        "import_rejected": "import os",
        "file_access_rejected": 'open("/etc/passwd")',
        "attribute_traversal_rejected": "bpmn.__class__.__subclasses__()",
        "loop_rejected": "while True:\n    pass",
        "exec_rejected": 'exec("pass")',
        "eval_rejected": 'eval("1")',
        "code_limit_enforced": "#" * 65537,
    }
    for name, code in negative.items():
        try:
            await runner.run(code, "")
        except PipelineError as exc:
            checks[name] = exc.issue.code == "CODE_POLICY"
        else:
            checks[name] = False
    async with httpx.AsyncClient(timeout=7, trust_env=False) as client:
        response = await client.post("http://worker:8081/run", content=b" " * 262145)
        checks["http_input_limit"] = (
            response.status_code == 413 and response.json().get("error", {}).get("code") == "INPUT_LIMIT"
        )
    checks["healthy_after_rejections"] = await runner.health()
    print(json.dumps({"checks": checks, "mode": "fixture_real_container"}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
