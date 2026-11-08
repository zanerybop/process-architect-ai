"""Real LLM-only probe; saves generated Python as data without interpreting it."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from backend.app.config import Settings
from backend.app.domain import PipelineError
from backend.app.llm import BuildEnvelope, LiveProvider
from backend.app.prompts import PROMPT_VERSION
from scripts.live_smoke import DESCRIPTION


async def main(target: Path):
    settings = Settings()
    target.mkdir(parents=True, exist_ok=True)
    report = {
        "scope": "LLM provider only; generated Python is stored, never executed",
        "timestamp": datetime.now(UTC).isoformat(),
        "model": settings.llm_model,
        "prompt_version": PROMPT_VERSION,
        "response_format": settings.llm_response_format,
        "reasoning_effort": settings.llm_reasoning_effort,
        "description": DESCRIPTION,
        "checks": {
            "live_envelope": "NOT_RUN",
            "python_interpretation": "NOT_RUN",
            "ir": "NOT_RUN",
            "xsd": "NOT_RUN",
            "browser_import": "NOT_RUN",
            "meaning": "NOT_RUN",
        },
        "full_pipeline": "NOT_RUN",
    }
    try:
        if settings.app_mode != "live":
            raise PipelineError("LIVE_REQUIRED", "Установите APP_MODE=live для проверки модели.")
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
        report["transport_profile"] = provider.profile_metadata
        result = await provider.generate(DESCRIPTION)
        # Each run gets its own directory; an unsuccessful rerun cannot leave an
        # earlier generated.py beside a new failed report and imply fresh success.
        run_dir = target / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        run_dir.mkdir()
        (run_dir / "envelope.json").write_text(
            result.envelope.model_dump_json(indent=2), encoding="utf-8"
        )
        built = isinstance(result.envelope, BuildEnvelope)
        if built:
            (run_dir / "generated.py").write_text(result.envelope.python_code, encoding="utf-8")
        report.update({
            "status": "PASS" if built else "FAIL",
            "provider": result.provider,
            "envelope_mode": result.envelope.mode,
            "usage": result.usage,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
            "artifacts": str(run_dir),
        })
        report["checks"]["live_envelope"] = "PASS"
        if not built:
            report["reason"] = "Для этого полного описания ожидался build с Python."
    except PipelineError as exc:
        report.update({"status": "FAIL", "error": exc.issue.model_dump()})
    (target / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"LLM provider: {report['status']}; full pipeline: NOT_RUN")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/live-provider"))
    raise SystemExit(asyncio.run(main(parser.parse_args().output_dir)))
