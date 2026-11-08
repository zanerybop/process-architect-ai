"""Loopback-only browser test API with explicit trusted-fixture dependency injection.

This module is outside application code and does not demonstrate Docker isolation
or real LLM generation. Production configuration cannot select this runner.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import uvicorn

from backend.app.auth import AuthRateLimit, AuthStore
from backend.app.config import Settings
from worker.interpreter import interpret


class TrustedFixtureRunner:
    async def health(self):
        return True

    async def run(self, code: str, source_text: str):
        return interpret(code, source_text)


class FixtureAccountRateLimit(AuthRateLimit):
    """Isolate generated test accounts sharing one loopback client address.

    Production IP throttling is exercised separately in tests/test_auth.py.
    Password checks, sessions and the per-account attempt budget remain active.
    """

    def check(self, client: str, username: str):
        super().check(f"{client}:{username.casefold()}", username)


def fixture_directory():
    root = Path(tempfile.gettempdir())
    directory = Path(os.environ["BROWSER_TEST_DATA_DIR"]).resolve()
    if directory.parent != root.resolve() or not directory.name.startswith("bpmn-browser-"):
        raise ValueError("Browser fixture data must use its isolated bpmn-browser-* temporary directory.")
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provision-account", action="store_true")
    args = parser.parse_args()
    directory = fixture_directory()
    marker = directory / "fixture-only.json"
    if args.provision_account:
        if json.loads(marker.read_text(encoding="utf-8")) != {"test_transport": "trusted_fixture_unit"}:
            raise ValueError("Not an initialized browser fixture directory.")
        credentials = json.load(sys.stdin)
        AuthStore(directory).create_user(credentials["username"], credentials["password"])
        return
    directory.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"test_transport": "trusted_fixture_unit"}), encoding="utf-8")
    # main exports a default app at import time; keep even that initial store
    # inside the fixture directory. Provisioning never imports the API module.
    os.environ["DATA_DIR"] = str(directory)
    from backend.app.main import create_app

    settings = Settings(
        _env_file=None,
        app_mode="mock",
        data_dir=directory,
        llm_api_key="",
        llm_model="",
        llm_api_base_url="",
    )
    app = create_app(settings=settings, runner=TrustedFixtureRunner())
    app.state.auth.rate_limit = FixtureAccountRateLimit()
    app.state.test_transport = "trusted_fixture_unit"
    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
