"""Real model -> existing isolated Compose worker -> document compiler probe.

The application GenerationService runs unchanged. A probe-only transport reaches
the private worker through the API container; model code is JSON stdin, never a
shell command or host interpreter input. No model/worker fallback is available.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from backend.app.config import Settings
from backend.app.domain import PipelineError, ProcessIR
from backend.app.llm import LiveProvider
from backend.app.service import GenerationService
from backend.app.storage import Storage

WORKER_REQUEST = (
    "import sys,httpx; "
    "payload=sys.stdin.buffer.read(); "
    "response=httpx.post('http://worker:8081/run',content=payload,"
    "headers={'Content-Type':'application/json'},timeout=15,trust_env=False); "
    "response.raise_for_status(); sys.stdout.buffer.write(response.content)"
)


class PrivateWorkerProbe:
    async def run(self, code: str, source_text: str) -> ProcessIR:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "compose",
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            WORKER_REQUEST,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = json.dumps({"code": code, "source_text": source_text}, ensure_ascii=False).encode()
        try:
            output, _ = await asyncio.wait_for(process.communicate(payload), timeout=25)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise PipelineError(
                "WORKER_TIMEOUT", "Истекло время обращения к изолированному worker."
            ) from None
        if process.returncode:
            raise PipelineError("RUNNER_UNAVAILABLE", "Compose transport не достиг изолированного worker.")
        if len(output) > 1024**2:
            raise PipelineError("OUTPUT_LIMIT", "Превышен лимит ответа worker.")
        try:
            data = json.loads(output)
            if "error" in data:
                error = data["error"]
                raise PipelineError(error["code"], error["message"], error.get("element_ids"))
            return ProcessIR.model_validate(data["ir"])
        except (ValueError, KeyError, TypeError):
            raise PipelineError("WORKER_PROTOCOL", "Недопустимый ответ изолированного worker.") from None


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/full-bpmn/live"))
    args = parser.parse_args()
    settings = Settings()
    if settings.app_mode != "live":
        raise RuntimeError("Для этого probe нужен APP_MODE=live.")
    provider = LiveProvider(
        base_url=settings.llm_api_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
        token_parameter=settings.llm_token_parameter,
        response_format=settings.llm_response_format,
        reasoning_effort=settings.llm_reasoning_effort,
        profile=settings.llm_provider_profile,
        auth_scheme=settings.llm_auth_scheme,
        project=settings.llm_project,
    )
    directory = args.output / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    service = GenerationService(settings, provider, PrivateWorkerProbe(), Storage(directory))
    result = await service.generate(args.source.read_text(encoding="utf-8"))
    report = {
        "scope": "Real model and isolated worker; application service; browser checked separately",
        "status": result["status"],
        "model": provider.model,
        "attempt_count": result["attempt_count"],
        "checks": result["checks"],
        "issues": result["issues"],
        "timings": result["timings"],
        "id": result["id"],
    }
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "attempt_count", "checks", "timings")}))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
