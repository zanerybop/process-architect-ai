"""Explicit live gate. Saves only permitted synthetic text and generated artifacts."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from backend.app.config import Settings

DESCRIPTION = (
    "Техник получает заявку на поверку датчика, регистрирует её в журнале. "
    "После этого метролог проверяет данные прибора. Если данные полные, метролог "
    "назначает дату поверки и завершает процесс. Если данные неполные, техник "
    "запрашивает уточнения и возвращается к регистрации заявки."
)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-test-account", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Адрес обычного API приложения")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/live"))
    args = parser.parse_args()
    if args.create_test_account:
        parser.error(
            "Регистрация закрыта. Создайте аккаунт локально через backend.app.manage_users "
            "и задайте TEST_USERNAME/TEST_PASSWORD."
        )
    return args


def credentials():
    username, password = os.environ.get("TEST_USERNAME"), os.environ.get("TEST_PASSWORD")
    if not username or not password:
        raise ValueError(
            "Нужны TEST_USERNAME и TEST_PASSWORD существующего аккаунта. "
            "Создайте его локально через backend.app.manage_users."
        )
    return {"username": username, "password": password}


async def main():
    args = arguments()
    settings = Settings()
    target = args.output_dir
    target.mkdir(parents=True, exist_ok=True)
    if settings.app_mode != "live" or not all(
        (settings.llm_api_base_url, settings.llm_model, settings.llm_api_key.get_secret_value())
    ):
        report = {
            "status": "BLOCKED",
            "reason": "Нужны APP_MODE=live и LLM_API_BASE_URL/LLM_MODEL/LLM_API_KEY.",
        }
    else:
        try:
            login = credentials()
            async with httpx.AsyncClient(base_url=args.url, timeout=400, trust_env=False) as client:
                auth = await client.post(
                    "/api/auth/login",
                    json=login,
                    headers={"X-Requested-With": "ProcessArchitect"},
                )
                auth.raise_for_status()
                response = await client.post(
                    "/api/generate", json={"description": DESCRIPTION},
                    headers={"X-CSRF-Token": auth.json()["csrf_token"]},
                )
                response.raise_for_status()
                report = response.json()
                report["authentication"] = {"method": "existing_account"}
        except ValueError as error:
            report = {"status": "BLOCKED", "reason": str(error)}
        except httpx.HTTPError:
            report = {
                "status": "BLOCKED",
                "reason": "Локальный API недоступен или завершил запрос с ошибкой.",
            }
    (target / "smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report["status"])
    return 0 if report.get("status") == "ready" and report.get("mode") == "live" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
