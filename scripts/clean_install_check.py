"""Repeat acceptance in a new source copy with fresh Python/npm environments.

No existing workspace is deleted or reused. Global package caches and the
explicit Chromium executable may be reused; this is not a fresh OS/container.
"""

import argparse
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "clean-install"
EXCLUDED_DIRS = {
    ".venv",
    "node_modules",
    "dist",
    ".git",
    "artifacts",
    "release",
    "local_data",
    "logs",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".cache",
    "test-results",
    "playwright-report",
}


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def copy_sources(target):
    manifest = []
    for directory, names, filenames in os.walk(ROOT, followlinks=False):
        parent = Path(directory)
        names[:] = [name for name in names if name not in EXCLUDED_DIRS and not is_link(parent / name)]
        for name in filenames:
            original = parent / name
            if (
                (name.startswith(".env") and name != ".env.example")
                or name.endswith((".pyc", ".pyo", ".log", ".tsbuildinfo"))
                or is_link(original)
            ):
                continue
            relative = original.relative_to(ROOT)
            copied = target / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, copied)
            manifest.append(
                {"path": relative.as_posix(), "sha256": hashlib.sha256(copied.read_bytes()).hexdigest()}
            )
    return manifest


def write_report(report):
    (OUTPUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline=""
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace", default="workspace", help="Новое имя каталога внутри artifacts/clean-install"
    )
    parser.add_argument("--browser-executable", default=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", ""))
    parser.add_argument("--without-browser", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = (OUTPUT / args.workspace).resolve()
    if target.parent != OUTPUT.resolve() or not target.name.startswith("workspace") or target.exists():
        parser.error(
            "Нужен новый каталог workspace* непосредственно внутри artifacts/clean-install; удаления нет."
        )
    target.mkdir(mode=0o700)
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "source": str(ROOT),
        "workspace": str(target),
        "scope": "Новая копия и fresh .venv/node_modules; существующие OS, global uv/npm caches и Chromium.",
        "excluded_directories": sorted(EXCLUDED_DIRS),
        "environment_files": "Только .env.example",
        "commands": [],
        "status": "RUNNING",
    }
    manifest = copy_sources(target)
    (OUTPUT / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    report["copied_file_count"] = len(manifest)
    report["initial_dependency_directories_absent"] = (
        not (target / ".venv").exists() and not (target / "frontend" / "node_modules").exists()
    )
    write_report(report)
    env = os.environ.copy()
    # Make this explicitly offline even when the invoking shell contains live configuration.
    for name in list(env):
        if name.upper().startswith("LLM_") or name.upper() in {
            "APP_MODE",
            "DATA_DIR",
            "WORKER_URL",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
        }:
            env.pop(name)
    if args.browser_executable:
        env["PLAYWRIGHT_CHROMIUM_EXECUTABLE"] = args.browser_executable
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    uv = shutil.which("uv")
    node = shutil.which("node")

    def run(name, command, cwd=target, timeout=180, reason=None):
        index = len(report["commands"]) + 1
        entry = {"name": name, "command": command, "cwd": str(cwd), "status": "NOT_RUN"}
        report["commands"].append(entry)
        if reason or any(part is None for part in command):
            entry.update(status="BLOCKED", reason=reason or "Не найден инструмент команды.")
            write_report(report)
            return False
        print(f"[{index}] {name}: {' '.join(command)}", flush=True)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            stdout, stderr = completed.stdout, completed.stderr
            entry.update(
                exit_code=completed.returncode, status="PASS" if completed.returncode == 0 else "FAIL"
            )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or b""
            stderr = error.stderr or b""
            stdout = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout
            stderr = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
            entry.update(status="FAIL", exit_code=None, reason=f"Timeout {timeout} seconds")
        except OSError as error:
            stdout, stderr = "", str(error)
            entry.update(status="BLOCKED", exit_code=None, reason="Не удалось запустить инструмент.")
        entry["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        for stream, content in (("stdout", stdout), ("stderr", stderr)):
            filename = f"{index:02d}-{name}.{stream}.log"
            (OUTPUT / filename).write_text(content, encoding="utf-8", newline="")
            entry[f"{stream}_log"] = filename
        write_report(report)
        print(
            f"    {entry['status']}, exit={entry.get('exit_code')}, {entry['elapsed_seconds']} s", flush=True
        )
        return entry["status"] == "PASS"

    run("uv-version", [uv, "--version"])
    run("node-version", [node, "--version"])
    run("npm-version", [npm, "--version"])
    python_ready = run("uv-sync", [uv, "sync", "--locked"], timeout=300)
    frontend = target / "frontend"
    node_ready = run("npm-ci", [npm, "ci"], cwd=frontend, timeout=300)
    python_block = None if python_ready else "uv sync --locked не прошёл"
    node_block = None if node_ready else "npm ci не прошёл"
    run("python-version", [uv, "run", "--locked", "python", "--version"], reason=python_block)
    run(
        "pytest",
        [uv, "run", "--locked", "pytest", "-q", "--junitxml=artifacts/pytest.xml",
         "--basetemp", str(ROOT / "artifacts" / ("cq-" + uuid.uuid4().hex[:8]))],
        reason=python_block,
        timeout=600,
    )
    run(
        "ruff",
        [uv, "run", "--locked", "ruff", "check", "backend", "worker", "tests", "scripts"],
        reason=python_block,
    )
    fixtures = run(
        "compile-fixtures",
        [uv, "run", "--locked", "python", "-m", "scripts.compile_fixtures"],
        reason=python_block,
    )
    for name in ("test", "typecheck", "lint", "build"):
        run(f"frontend-{name}", [npm, "run", name], cwd=frontend, reason=node_block, timeout=180)
    browser_block = None
    if args.without_browser:
        browser_block = "Браузерный этап явно выключен флагом --without-browser"
    elif not (python_ready and node_ready and fixtures):
        browser_block = "Не прошли prerequisites Python/npm/fixtures"
    else:
        for port in (5173, 8010):
            with socket.socket() as connection:
                connection.settimeout(1)
                if connection.connect_ex(("127.0.0.1", port)) == 0:
                    browser_block = (
                        f"Порт {port} уже занят; чужой сервер не используется и не останавливается"
                    )
    run("browser", [npm, "run", "test:browser"], cwd=frontend, reason=browser_block, timeout=300)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["status"] = (
        "PASS" if all(entry["status"] == "PASS" for entry in report["commands"]) else "INCOMPLETE"
    )
    write_report(report)
    print(f"Clean install: {report['status']}; report: {OUTPUT / 'report.json'}", flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
