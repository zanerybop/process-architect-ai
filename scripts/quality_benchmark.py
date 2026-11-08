"""Repeatable, authenticated live API benchmark; never executes generated Python.

Named-property checks are a deliberately incomplete regression oracle, not an expert
semantic judgment. Every request (including errors) remains in the denominator.
Authentication is held in memory. Run only against an explicitly authorized model.
"""

import argparse
import hashlib
import json
import math
import re
import secrets
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
TASK_TYPES = {"task", "userTask", "serviceTask", "sendTask", "receiveTask"}


def _matches(pattern, text):
    return bool(re.search(pattern, text.replace("ё", "е"), re.IGNORECASE))


def _reachable(seeds, flows, excluded_nodes=(), excluded_flows=()):
    seen, pending = set(), list(seeds)
    outgoing = {}
    for flow in flows:
        if flow.get("kind", "sequence") == "sequence" and flow["id"] not in excluded_flows:
            outgoing.setdefault(flow["source"], []).append(flow["target"])
    while pending:
        node = pending.pop()
        if node in seen or node in excluded_nodes:
            continue
        seen.add(node)
        pending.extend(outgoing.get(node, []))
    return seen


def _has_cycle(nodes, flows):
    incoming = {node["id"]: 0 for node in nodes}
    outgoing = {node_id: [] for node_id in incoming}
    for flow in flows:
        if flow.get("kind", "sequence") != "sequence":
            continue
        if flow["source"] in incoming and flow["target"] in incoming:
            incoming[flow["target"]] += 1
            outgoing[flow["source"]].append(flow["target"])
    pending = [node_id for node_id, count in incoming.items() if count == 0]
    visited = 0
    while pending:
        visited += 1
        for target in outgoing[pending.pop()]:
            incoming[target] -= 1
            if incoming[target] == 0:
                pending.append(target)
    return visited < len(incoming)


def evaluate_properties(case: dict, response: dict) -> list[dict]:
    """Evaluate only the explicitly annotated property, retaining matched IDs."""
    ir = response.get("ir") or {}
    nodes, flows = ir.get("nodes", []), ir.get("flows", [])
    actions = [node for node in nodes if node.get("type") in TASK_TYPES]
    lanes = {lane["id"]: lane.get("name", "") for lane in ir.get("lanes", [])}
    starts = [node["id"] for node in nodes if node.get("type") == "startEvent"]

    def select(pattern):
        return [node["id"] for node in actions if _matches(pattern, node.get("name", ""))]

    def check(prop):
        kind = prop["kind"]
        matched, observed = [], None
        if kind in {"action", "forbidden_action"}:
            matched = [
                node["id"]
                for node in actions
                if _matches(prop["pattern"], node.get("name", ""))
                and (
                    not prop.get("role_pattern")
                    or _matches(prop["role_pattern"], lanes.get(node.get("lane_id"), ""))
                )
            ]
            passed = bool(matched) if kind == "action" else not matched
        elif kind in {"role", "pool"}:
            entities = ir.get("lanes" if kind == "role" else "participants", [])
            matched = [
                entity["id"] for entity in entities if _matches(prop["pattern"], entity.get("name", ""))
            ]
            passed = bool(matched)
        elif kind == "count":
            metric = prop["metric"]
            observed = {
                "xor": sum(node.get("type") == "exclusiveGateway" for node in nodes),
                "and": sum(node.get("type") == "parallelGateway" for node in nodes),
                "tasks": len(actions),
                "pools": len(ir.get("participants", [])),
                "messages": sum(flow.get("kind") == "message" for flow in flows),
                "ends": sum(node.get("type") == "endEvent" for node in nodes),
            }[metric]
            passed = prop.get("min", 0) <= observed <= prop.get("max", math.inf)
        elif kind == "cycle":
            observed = _has_cycle(nodes, flows)
            passed = observed is prop["expected"]
        elif kind == "order":
            before, after = select(prop["before"]), select(prop["after"])
            reached = _reachable(before, flows)
            bypass = _reachable(starts, flows, excluded_nodes=before)
            matched = [*before, *after]
            passed = bool(before and after) and all(node in reached and node not in bypass for node in after)
        elif kind == "parallel_join":
            branches = [select(pattern) for pattern in prop["branches"]]
            after = select(prop["after"])
            passed = False
            for join in (node for node in nodes if node.get("type") == "parallelGateway"):
                join_id = join["id"]
                incoming = [
                    flow
                    for flow in flows
                    if flow.get("kind", "sequence") == "sequence" and flow["target"] == join_id
                ]
                if len(incoming) < 2 or not all(branches) or not after:
                    continue
                if not all(join_id in _reachable(branch, flows) for branch in branches):
                    continue
                if not all(node in _reachable([join_id], flows) for node in after):
                    continue
                if any(
                    set(after) & _reachable(branch, flows, excluded_nodes={join_id}) for branch in branches
                ):
                    continue
                matched, passed = [join_id, *after, *(node for branch in branches for node in branch)], True
                break
        elif kind == "condition_on_path":
            targets = select(prop["target"])
            matched = [
                flow["id"]
                for flow in flows
                if flow.get("kind", "sequence") == "sequence"
                and _matches(prop["pattern"], flow.get("name", "") + " " + (flow.get("condition") or ""))
            ]
            bypass = _reachable(starts, flows, excluded_flows=matched)
            passed = bool(targets and matched) and not set(targets) & bypass
        elif kind == "literal":
            passed = any(prop["text"] in entity.get("name", "") for entity in [*nodes, *flows])
        elif kind == "question":
            questions = [
                item.get("question", "") if isinstance(item, dict) else str(item)
                for item in response.get("questions", [])
            ]
            passed = any(_matches(prop["pattern"], question) for question in questions)
        else:
            raise ValueError(f"Unknown annotated property: {kind}")
        # No IR cannot count as a passing negative/count/cycle business check.
        if kind != "question" and not nodes:
            passed = False
        return {
            "id": prop["id"],
            "kind": kind,
            "critical": prop.get("critical", True),
            "passed": bool(passed),
            "matched_ids": matched,
            "observed": observed,
        }

    return [check(prop) for prop in case.get("semantic_named_properties", [])]


def evaluate_result(case: dict, response: dict, elapsed: float) -> dict:
    properties = evaluate_properties(case, response)
    expected_ready = case["expected_status"] == "ready"
    checks = response.get("checks") or {}
    technical = all(checks.get(key) == "passed" for key in ("code", "graph", "xsd", "layout"))
    behavior = checks.get("behavior", "not_run")
    critical = [prop for prop in properties if prop["critical"]]
    critical_pass = all(prop["passed"] for prop in critical) if critical else None
    status_match = response.get("status") == case["expected_status"]
    return {
        "case_id": case["id"],
        "expected_status": case["expected_status"],
        "actual_status": response.get("status", "error"),
        "status_match": status_match,
        "expected_ready": expected_ready,
        "core_technical_pass": technical if expected_ready else None,
        "behavior": behavior,
        "browser_import": "not_run",
        "named_properties": properties,
        "critical_named_properties_pass": critical_pass,
        "expert_semantic_review": "NOT_REVIEWED",
        "candidate_success": status_match
        and critical_pass is not False
        and (
            not expected_ready
            or (technical and behavior in {"passed", "inconclusive"} and critical_pass is not False)
        ),
        "attempt_count": response.get("attempt_count"),
        "first_attempt": response.get("attempt_count") == 1,
        "elapsed_seconds": round(elapsed, 4),
        "error_codes": [
            issue.get("code") for issue in response.get("issues", []) if issue.get("severity") == "error"
        ],
    }


def summarize(records: list[dict], planned: int) -> dict:
    count = len(records)
    positive = [record for record in records if record["expected_ready"]]
    timings = sorted(record["elapsed_seconds"] for record in records)
    configurations = {}
    for record in records:
        key = (record.get("provider"), record.get("model"), record.get("prompt_sha256"))
        configurations[key] = configurations.get(key, 0) + 1

    def rate(numerator, denominator):
        return {
            "numerator": numerator,
            "denominator": denominator,
            "rate": round(numerator / denominator, 4) if denominator else None,
        }

    return {
        "planned_runs": planned,
        "completed_runs": count,
        "not_run": planned - count,
        "status_match": rate(sum(record["status_match"] for record in records), count),
        "candidate_success": rate(sum(record["candidate_success"] for record in records), count),
        "first_attempt_candidate_success": rate(
            sum(record["candidate_success"] and record["first_attempt"] for record in records), count
        ),
        "core_technical_positive": rate(
            sum(record["core_technical_pass"] for record in positive), len(positive)
        ),
        "critical_named_properties_positive": rate(
            sum(record["critical_named_properties_pass"] is True for record in positive), len(positive)
        ),
        "behavior_counts": {
            state: sum(record["behavior"] == state for record in positive)
            for state in ("passed", "failed", "inconclusive", "not_run")
        },
        "repair_runs": sum((record["attempt_count"] or 0) > 1 for record in records),
        "error_runs": sum(record["actual_status"] == "error" for record in records),
        "metadata_captured": rate(sum(record.get("metadata_captured", False) for record in records), count),
        "timing_all_completed_including_failures": {
            "p50_seconds": round(statistics.median(timings), 4) if timings else None,
            "p95_seconds": timings[math.ceil(len(timings) * 0.95) - 1] if timings else None,
            "p95_method": "nearest_rank",
            "sample_count": len(timings),
        },
        "expert_semantic_review": "NOT_REVIEWED",
        "configurations": [
            {"provider": key[0], "model": key[1], "prompt_sha256": key[2], "runs": value}
            for key, value in configurations.items()
        ],
        "limitations": [
            "Регулярные выражения и графовые правила проверяют размеченные признаки, не весь бизнес-смысл.",
            "Все завершённые запросы, включая ошибки, входят в знаменатель; незапущенные показаны отдельно.",
            "Импорт в браузер, работа аналитика и экономический эффект этим прогоном не измеряются.",
            "Число повторов указано в run.json; результаты выборки не гарантируют качество будущих ответов.",
        ],
        "runs": records,
    }


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def case_fingerprint(case):
    return hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_complete_run(directory: Path, case: dict, repeat: int) -> dict | None:
    """A completion marker is published last; partial responses are never silently reused."""
    marker = directory / "complete.json"
    if not marker.is_file():
        return None
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        if manifest["case_fingerprint"] != case_fingerprint(case) or manifest["repeat"] != repeat:
            raise ValueError("Case or repetition changed; use a new output directory")
        for filename, expected_hash in manifest["files"].items():
            if Path(filename).name != filename:
                raise ValueError("Invalid artifact filename")
            if hashlib.sha256((directory / filename).read_bytes()).hexdigest() != expected_hash:
                raise ValueError("Completed benchmark artifact changed; use a new output directory")
        return json.loads((directory / "evaluation.json").read_text(encoding="utf-8"))
    except (KeyError, json.JSONDecodeError, OSError) as error:
        raise ValueError(
            "Incomplete or damaged benchmark record; preserve it and choose a new output"
        ) from error


def provision_local_account(api_url: str):
    if urlsplit(api_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            "Automatic benchmark account provisioning is supported only for the local Compose API"
        )
    username, password = "bench_" + secrets.token_hex(5), secrets.token_urlsafe(32)
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "backend.app.manage_users",
            "create",
            username,
            "--password-stdin",
        ],
        input=password + "\n",
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(
            "Не удалось создать локальный benchmark-аккаунт; проверьте работающий Compose API."
        )
    return username, password


def run_benchmark(
    cases: list[dict],
    repeats: int,
    output: Path,
    api_url: str,
    *,
    min_interval=5.1,
    stop_after=None,
):
    output.mkdir(parents=True, exist_ok=True)
    run_info = output / "run.json"
    corpus_hash = hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if run_info.exists():
        previous = json.loads(run_info.read_text(encoding="utf-8"))
        if previous["corpus_sha256"] != corpus_hash or previous["repeats"] != repeats:
            raise ValueError("Корпус или число повторов изменилось; используйте новую папку результата.")
    else:
        _write_json(
            run_info,
            {
                "created_at": datetime.now(UTC).isoformat(),
                "corpus_sha256": corpus_hash,
                "repeats": repeats,
                "mode": "live",
                "authentication_saved": False,
            },
        )
        _write_json(output / "cases.json", {"cases": cases})
    records, pending = [], []
    # Round-robin repeats avoid placing all repetitions of one process next to each other.
    for repeat in range(1, repeats + 1):
        for case in cases:
            directory = output / f"{case['id']}-{repeat}"
            possible = [directory, *sorted(output.glob(f"{case['id']}-{repeat}-retry-*"))]
            complete = [record for path in possible if (record := load_complete_run(path, case, repeat))]
            if len(complete) > 1:
                raise ValueError("Более одной завершённой записи на повтор; не выбирайте результат вручную.")
            existing = complete[0] if complete else None
            if existing:
                records.append(existing)
            else:
                if directory.exists() and any(directory.iterdir()):
                    # Retain partial attempts separately; do not overwrite their evidence.
                    directory = output / f"{case['id']}-{repeat}-retry-{secrets.token_hex(3)}"
                pending.append((case, repeat, directory))
    _write_json(output / "summary.json", summarize(records, len(cases) * repeats))
    if not pending:
        return summarize(records, len(cases) * repeats)
    stop_reason = None

    def stop_requested():
        if (output / "STOP").exists():
            return "STOP marker requested; current request was completed and saved"
        if stop_after is not None and len(records) >= stop_after:
            return f"Diagnostic cutoff after {stop_after} completed requests"
        return None

    if stop_reason := stop_requested():
        summary = summarize(records, len(cases) * repeats) | {"stop_reason": stop_reason}
        _write_json(output / "summary.json", summary)
        return summary
    with httpx.Client(
        base_url=api_url.rstrip("/"),
        timeout=httpx.Timeout(420, connect=10),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        health_response = client.get("/api/health")
        health_response.raise_for_status()
        health = health_response.json()
        _write_json(output / "health.json", health)
        if health.get("mode") != "live" or not health.get("generation_available"):
            raise RuntimeError("Для benchmark требуется доступный live API с worker; mock не принимается.")
        username, password = provision_local_account(api_url)
        login = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"X-Requested-With": "ProcessArchitect"},
        )
        password = None
        login.raise_for_status()
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        last_start = 0.0
        try:
            for case, repeat, directory in pending:
                if stop_reason := stop_requested():
                    break
                time.sleep(max(0, min_interval - (time.monotonic() - last_start)))
                if stop_reason := stop_requested():
                    break
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "source.txt").write_text(case["text"], encoding="utf-8")
                last_start = time.monotonic()
                try:
                    http_response = client.post("/api/generate", json={"description": case["text"]})
                    http_status = http_response.status_code
                    try:
                        response = http_response.json()
                        if not isinstance(response, dict):
                            raise ValueError("Expected API object")
                    except ValueError:
                        (directory / "response.raw").write_bytes(http_response.content)
                        response = {
                            "status": "error",
                            "issues": [
                                {
                                    "code": "BENCHMARK_INVALID_RESPONSE",
                                    "severity": "error",
                                    "message": "API response is not a JSON object.",
                                }
                            ],
                        }
                except (httpx.HTTPError, ValueError):
                    http_status = None
                    response = {
                        "status": "error",
                        "issues": [
                            {
                                "code": "BENCHMARK_TRANSPORT",
                                "severity": "error",
                                "message": "API response unavailable or invalid.",
                            }
                        ],
                    }
                elapsed = time.monotonic() - last_start
                _write_json(directory / "response.json", response)
                if response.get("python_code"):
                    (directory / "generated.py").write_text(response["python_code"], encoding="utf-8")
                if response.get("ir"):
                    _write_json(directory / "process_ir.json", response["ir"])
                if response.get("bpmn_xml"):
                    (directory / "generated.bpmn").write_text(response["bpmn_xml"], encoding="utf-8")
                metadata = None
                generation_id = response.get("id", "")
                if re.fullmatch(r"[0-9a-f]{32}", generation_id):
                    try:
                        artifact = client.get(f"/api/generations/{generation_id}/artifacts/metadata")
                        artifact.raise_for_status()
                        metadata = artifact.json()
                        _write_json(directory / "metadata.json", metadata)
                    except (httpx.HTTPError, ValueError):
                        _write_json(directory / "metadata-error.json", {"status": "unavailable"})
                record = evaluate_result(case, response, elapsed)
                if http_status != 200:
                    record["candidate_success"] = False
                record.update(
                    {
                        "repeat": repeat,
                        "http_status": http_status,
                        "artifact_directory": directory.name,
                        "provider": metadata.get("provider") if metadata else None,
                        "model": metadata.get("model") if metadata else None,
                        "prompt_version": metadata.get("prompt_version") if metadata else None,
                        "prompt_sha256": metadata.get("prompt_sha256") if metadata else None,
                        "metadata_captured": metadata is not None,
                    }
                )
                _write_json(directory / "evaluation.json", record)
                _write_json(
                    directory / "complete.json",
                    {
                        "case_fingerprint": case_fingerprint(case),
                        "repeat": repeat,
                        "files": {
                            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in directory.iterdir()
                            if path.is_file() and path.name != "complete.json"
                        },
                    },
                )
                records.append(record)
                _write_json(output / "summary.json", summarize(records, len(cases) * repeats))
                print(
                    json.dumps(
                        {
                            "completed": len(records),
                            "total": len(cases) * repeats,
                            "case": case["id"],
                            "repeat": repeat,
                            "status": record["actual_status"],
                            "candidate_success": record["candidate_success"],
                            "seconds": round(elapsed, 2),
                        }
                    ),
                    flush=True,
                )
        finally:
            try:
                client.post("/api/auth/logout")
            except httpx.HTTPError:
                pass
    summary = summarize(records, len(cases) * repeats)
    if stop_reason:
        summary["stop_reason"] = stop_reason
    _write_json(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--casesfile", type=Path, default=ROOT / "examples/quality_cases.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8088")
    parser.add_argument(
        "--stop-after",
        type=int,
        help="Stop after N fully saved requests; also honours OUTPUT/STOP between requests",
    )
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("repeats must be between 1 and 20")
    if args.stop_after is not None and args.stop_after < 1:
        parser.error("stop-after must be positive")
    cases = json.loads(args.casesfile.read_text(encoding="utf-8"))["cases"]
    if not cases:
        parser.error("The benchmark corpus must contain at least one case")
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or not all(re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", value) for value in ids):
        parser.error("Case IDs must be unique safe names")
    output = args.output or ROOT / "artifacts/quality/benchmark" / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    report = run_benchmark(cases, args.repeats, output, args.url, stop_after=args.stop_after)
    if report["not_run"]:
        return 2
    return 0 if report["candidate_success"]["numerator"] == report["planned_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
