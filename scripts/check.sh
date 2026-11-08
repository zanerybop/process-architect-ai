#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
uv run pytest -q --junitxml=artifacts/pytest.xml
uv run ruff check backend worker tests scripts
uv run python -m scripts.compile_fixtures
uv run python -m scripts.build_full_bpmn_fixtures
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:browser
