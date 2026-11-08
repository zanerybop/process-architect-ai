"""Read-only verification of the real Compose worker. Never substitutes a local runner."""

import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, stdin: str | None = None, timeout: int = 10):
    # Files avoid a child holding inherited PIPE handles after the client times out on Windows.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            ["docker", *args], cwd=ROOT, stdout=output, stderr=output,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        )
        try:
            process.communicate(input=stdin.encode("utf-8") if stdin is not None else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
            raise RuntimeError(
                f"Docker daemon или проверка worker не ответили за {timeout} секунд."
            ) from None
        output.seek(0)
        text = output.read(2_000_000).decode("utf-8", errors="replace")
        if process.returncode:
            raise RuntimeError("Команда Docker завершилась ошибкой; проверьте daemon и docker compose up.")
        return text.strip()


def main():
    target = ROOT / "artifacts/container"
    target.mkdir(parents=True, exist_ok=True)
    try:
        identifier = docker("compose", "ps", "-q", "worker")
        if not identifier:
            raise RuntimeError("Worker не запущен. Выполните docker compose up --build.")
        item = json.loads(docker("inspect", identifier))[0]
        host, config = item["HostConfig"], item["Config"]
        networks = list(item["NetworkSettings"]["Networks"])
        internal = all(json.loads(docker("network", "inspect", name))[0]["Internal"] for name in networks)
        environment_names = [entry.split("=", 1)[0] for entry in config.get("Env", [])]
        checks = {
            "non_root": config["User"] == "10001:10001",
            "read_only_rootfs": host["ReadonlyRootfs"],
            "no_new_privileges": "no-new-privileges:true" in host["SecurityOpt"],
            "drop_all_capabilities": "ALL" in host["CapDrop"],
            "memory_limit": 0 < host["Memory"] <= 256 * 1024**2,
            "cpu_limit": 0 < host["NanoCpus"] <= 1_000_000_000,
            "pids_limit": 0 < host["PidsLimit"] <= 32,
            "internal_network_only": bool(networks) and internal,
            "no_published_ports": not host["PortBindings"],
            "no_host_mounts": all(mount["Type"] == "tmpfs" for mount in item["Mounts"]),
            "no_llm_credentials": not any(
                name.startswith(("LLM_", "OPENAI_", "SSH_")) for name in environment_names
            ),
        }
        runtime = json.loads(
            docker(
                "compose",
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "import httpx,json; print(json.dumps(httpx.get('http://worker:8081/health').json()))",
            )
        )
        checks["runtime_sandbox_ready"] = runtime.get("ready") is True
        execution = json.loads(docker(
            "compose", "exec", "-T", "api", "python", "-",
            stdin=(ROOT / "scripts/container_worker_probe.py").read_text(encoding="utf-8"),
            timeout=45,
        ))
        checks.update(execution["checks"])
        report = {
            "status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "mode": "fixture_real_container",
            "note": "Конфигурация, health, реальный Python→IR→XML и отказы policy/input. "
            "Нагрузочный OOM/CPU stress и live LLM оцениваются отдельно.",
        }
    except (OSError, RuntimeError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as exc:
        report = {"status": "BLOCKED", "reason": str(exc)[:300]}
    (target / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Keep the machine-readable console report valid on Windows legacy encodings.
    # The saved UTF-8 report above retains readable Russian text.
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
