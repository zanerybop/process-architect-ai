"""Frozen, authenticated production API benchmark for document BPMN.

Never executes generated Python locally. Every request remains in the denominator.
XML assertions measure named properties; they are not complete semantic proof.
"""

import argparse
import hashlib
import json
import math
import re
import statistics
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from lxml import etree

from scripts.quality_benchmark import _write_json, provision_local_account

ROOT = Path(__file__).resolve().parents[1]
BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
ACTION_TAGS = {
    "task",
    "userTask",
    "serviceTask",
    "sendTask",
    "receiveTask",
    "manualTask",
    "businessRuleTask",
    "scriptTask",
    "callActivity",
    "choreographyTask",
}
SCOPE_TAGS = {"process", "subProcess", "transaction", "adHocSubProcess", "choreography"}


def matches(pattern, value):
    return bool(re.search(pattern, (value or "").replace("ё", "е"), re.IGNORECASE))


def tag(element):
    if not isinstance(element.tag, str):
        return ""
    name = etree.QName(element).localname
    explicit = element.get(f"{{{XSI}}}type", "").split(":")[-1]
    if name in {"flowElement", "rootElement", "artifact", "eventDefinition"} and explicit.startswith("t"):
        return explicit[1:2].lower() + explicit[2:]
    return name


class XmlOracle:
    def __init__(self, xml):
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        self.root = etree.fromstring(xml.encode(), parser=parser)
        if self.root.getroottree().docinfo.doctype:
            raise ValueError("DOCTYPE prohibited in benchmark XML")
        self.elements = [e for e in self.root.iter() if isinstance(e.tag, str)]
        self.by_id = {e.get("id"): e for e in self.elements if e.get("id")}
        self.flows = self.select("sequenceFlow")
        self.actions = [e for e in self.elements if tag(e) in ACTION_TAGS]

    def ref(self, context, value):
        """Resolve local QName without confusing external IDs with local suffixes."""
        if not value:
            return None
        prefix, separator, local = value.partition(":")
        if not separator:
            return self.by_id.get(value)
        namespace = context.nsmap.get(prefix)
        if namespace == self.root.get("targetNamespace"):
            return self.by_id.get(local)
        return None

    def select(self, name):
        return [e for e in self.elements if tag(e) == name]

    def action(self, pattern):
        return [e for e in self.actions if matches(pattern, e.get("name"))]

    def scope(self, element):
        return next((e for e in element.iterancestors() if tag(e) in SCOPE_TAGS), None)

    def reaches(self, seeds, excluded=()):
        pending, seen = list(seeds), set()
        excluded = set(excluded)
        outgoing = {}
        for flow in self.flows:
            source, target = self.ref(flow, flow.get("sourceRef")), self.ref(flow, flow.get("targetRef"))
            if source is not None and target is not None:
                outgoing.setdefault(source, []).append(target)
        while pending:
            current = pending.pop()
            if current in seen or current in excluded:
                continue
            seen.add(current)
            pending.extend(outgoing.get(current, []))
        return seen

    def role_names(self, action):
        roles = []
        for lane in self.select("lane"):
            if any(self.ref(child, child.text) is action for child in lane if tag(child) == "flowNodeRef"):
                roles.append(lane.get("name", ""))
        process = next((p for p in action.iterancestors() if tag(p) == "process"), None)
        if process is not None:
            for participant in self.select("participant"):
                if self.ref(participant, participant.get("processRef")) is process:
                    roles.append(participant.get("name", ""))
        for child in action.iter():
            if tag(child) == "resourceRef":
                resource = self.ref(child, child.text)
                if resource is not None:
                    roles.append(resource.get("name", ""))
            if tag(child) in {"humanPerformer", "potentialOwner", "performer"}:
                roles.append(child.get("name", ""))
        return " ".join(roles)

    def check(self, prop):
        kind, matched, observed = prop["kind"], [], None
        if kind == "count":
            matched = self.select(prop["tag"])
            observed = len(matched)
            passed = prop.get("min", 0) <= observed <= prop.get("max", math.inf)
        elif kind == "action":
            matched = [
                e
                for e in self.action(prop["pattern"])
                if not prop.get("role_pattern") or matches(prop["role_pattern"], self.role_names(e))
            ]
            passed = bool(matched)
        elif kind == "named":
            matched = [
                e for e in self.elements if tag(e) in prop["tags"] and matches(prop["pattern"], e.get("name"))
            ]
            passed = bool(matched)
        elif kind == "text":
            matched = [e for e in self.select(prop["tag"]) if matches(prop["pattern"], e.text)]
            passed = bool(matched)
        elif kind == "attribute":
            matched = [
                e
                for e in self.select(prop["tag"])
                if e.get(prop["attribute"], prop.get("default")) == prop["value"]
            ]
            passed = bool(matched)
        elif kind == "nested":
            matched = [
                e
                for e in self.select(prop["tag"])
                if any(tag(parent) == prop["ancestor"] for parent in e.iterancestors())
            ]
            passed = bool(matched)
        elif kind == "reference":
            matched = [
                e
                for e in self.select(prop["tag"])
                if (target := self.ref(e, e.get(prop["attribute"]))) is not None
                and tag(target) == prop["target_tag"]
            ]
            passed = bool(matched)
        elif kind == "boundary":
            matched = [
                e
                for e in self.select("boundaryEvent")
                if any(tag(child) == prop["definition"] for child in e)
                and (e.get("cancelActivity", "true") == "true") == prop["interrupting"]
                and (host := self.ref(e, e.get("attachedToRef"))) is not None
                and tag(host) == prop["host_tag"]
            ]
            passed = bool(matched)
        elif kind == "event_start":
            matched = [
                e
                for e in self.select("startEvent")
                if any(tag(child) == prop["definition"] for child in e)
                and (e.get("isInterrupting", "true") == "true") == prop["interrupting"]
                and e.getparent().get("triggeredByEvent") == "true"
            ]
            passed = bool(matched)
        elif kind == "sequence_scope":
            observed = []
            for flow in self.flows:
                source, target = self.ref(flow, flow.get("sourceRef")), self.ref(flow, flow.get("targetRef"))
                if source is None or target is None or self.scope(source) is not self.scope(target):
                    observed.append(flow.get("id"))
            passed = bool(self.flows) and not observed
        elif kind == "cycle":
            observed = False
            for flow in self.flows:
                source, target = self.ref(flow, flow.get("sourceRef")), self.ref(flow, flow.get("targetRef"))
                if source is not None and target is not None and source in self.reaches([target]):
                    observed = True
                    break
            passed = observed is prop["expected"]
        elif kind == "order":
            before, after = self.action(prop["before"]), self.action(prop["after"])
            starts = self.select("startEvent")
            reachable, bypass = self.reaches(before), self.reaches(starts, excluded=before)
            passed = bool(before and after and starts) and all(
                e in reachable and e not in bypass for e in after
            )
            matched = before + after
        elif kind == "parallel_join":
            branches, after = (
                [self.action(pattern) for pattern in prop["branches"]],
                self.action(prop["after"]),
            )
            passed = False
            for join in self.select("parallelGateway"):
                incoming = [f for f in self.flows if self.ref(f, f.get("targetRef")) is join]
                if len(incoming) < 2 or not all(branches) or not after:
                    continue
                if not all(join in self.reaches(branch) for branch in branches):
                    continue
                if not all(e in self.reaches([join]) for e in after):
                    continue
                if any(set(after) & self.reaches(branch, excluded=[join]) for branch in branches):
                    continue
                matched, passed = [join, *after], True
                break
        elif kind == "exclusive_branches":
            branches = [self.action(pattern) for pattern in prop["branches"]]
            passed = False
            for gateway in self.select("exclusiveGateway"):
                targets = [
                    self.ref(f, f.get("targetRef"))
                    for f in self.flows
                    if self.ref(f, f.get("sourceRef")) is gateway
                ]
                targets = [e for e in targets if e is not None]
                reaches = [self.reaches([e]) for e in targets]
                if len(targets) < 2 or not all(branches):
                    continue
                assignments = [
                    {i for i, reached in enumerate(reaches) if set(branch) & reached} for branch in branches
                ]
                passed = all(len(values) == 1 for values in assignments) and len(
                    set.union(*assignments)
                ) == len(branches)
                if passed:
                    matched = [gateway, *targets]
                    break
        elif kind == "compensation":
            passed = False
            for event in self.select("boundaryEvent"):
                if not any(tag(e) == "compensateEventDefinition" for e in event):
                    continue
                host = self.ref(event, event.get("attachedToRef"))
                if host is None or not matches(prop["host_pattern"], host.get("name")):
                    continue
                for association in self.select("association"):
                    handler = self.ref(association, association.get("targetRef"))
                    if self.ref(association, association.get("sourceRef")) is not event or handler is None:
                        continue
                    if (
                        not matches(prop["handler_pattern"], handler.get("name"))
                        or handler.get("isForCompensation") != "true"
                    ):
                        continue
                    if association.get("associationDirection") != "One":
                        continue
                    ordinary = any(
                        self.ref(f, f.get(side)) is handler
                        for f in self.flows
                        for side in ("sourceRef", "targetRef")
                    )
                    if not ordinary:
                        matched, passed = [host, event, association, handler], True
        elif kind == "choreography_task":
            matched = []
            for task in self.select("choreographyTask"):
                if not matches(prop["pattern"], task.get("name")):
                    continue
                initiator = self.ref(task, task.get("initiatingParticipantRef"))
                refs = [self.ref(e, e.text) for e in task if tag(e) == "participantRef"]
                flows = [self.ref(e, e.text) for e in task if tag(e) == "messageFlowRef"]
                if (
                    initiator is None
                    or not matches(prop["initiator"], initiator.get("name"))
                    or len(set(refs)) != 2
                    or None in refs
                ):
                    continue
                for flow in flows:
                    if flow is None or self.ref(flow, flow.get("sourceRef")) is not initiator:
                        continue
                    message = self.ref(flow, flow.get("messageRef"))
                    if message is not None and matches(prop["message"], message.get("name")):
                        matched.append(task)
            passed = bool(matched)
        else:
            raise ValueError(f"Unknown semantic property: {kind}")
        return {
            "id": prop["id"],
            "kind": kind,
            "passed": bool(passed),
            "observed": observed,
            "matched_ids": [e.get("id") for e in matched if e.get("id")],
        }


def evaluate_properties(case, response):
    xml = response.get("bpmn_xml")
    oracle = None
    if xml:
        try:
            oracle = XmlOracle(xml)
        except (ValueError, etree.XMLSyntaxError):
            pass
    results = []
    for prop in case["properties"]:
        if prop["kind"] == "question":
            questions = [
                item.get("question", "") if isinstance(item, dict) else str(item)
                for item in response.get("questions", [])
            ]
            results.append(
                {
                    "id": prop["id"],
                    "kind": "question",
                    "passed": any(matches(prop["pattern"], q) for q in questions),
                }
            )
        elif oracle is None:
            results.append(
                {"id": prop["id"], "kind": prop["kind"], "passed": False, "reason": "XML unavailable"}
            )
        else:
            results.append(oracle.check(prop))
    return results


def evaluate(case, response, elapsed, metadata):
    properties = evaluate_properties(case, response)
    checks = response.get("checks") or {}
    status_match = response.get("status") == case["expected_status"]
    technical = all(checks.get(key) == "passed" for key in ("code", "graph", "xsd", "layout"))
    semantic = bool(properties) and all(p["passed"] for p in properties)
    metadata = metadata or {}
    attempts = metadata.get("attempts", [])
    return {
        "id": case["id"],
        "title": case["title"],
        "group": case["group"],
        "expected_status": case["expected_status"],
        "actual_status": response.get("status", "error"),
        "status_match": status_match,
        "technical_pass": technical,
        "semantic_named_properties_pass": semantic,
        "candidate_success": status_match
        and semantic
        and (technical if case["expected_status"] == "ready" else True),
        "elapsed_seconds": round(elapsed, 4),
        "attempt_count": response.get("attempt_count", 0),
        "behavior": checks.get("behavior", "not_run"),
        "checks": checks,
        "properties": properties,
        "issue_codes": [issue.get("code") for issue in response.get("issues", [])],
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "prompt_version": metadata.get("prompt_version"),
        "prompt_sha256": metadata.get("prompt_sha256"),
        "schema_context_sha256": metadata.get("schema_context_sha256"),
        "generation_parameters": metadata.get("generation_parameters"),
        "attempts": [
            {
                k: a.get(k)
                for k in ("number", "mode", "error_codes", "usage", "stage_seconds", "schema_context_sha256")
            }
            for a in attempts
        ],
        "metadata_captured": bool(metadata),
        "document_ir": bool((response.get("ir") or {}).get("document")),
    }


def rate(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 4) if denominator else None,
    }


def timing(records):
    values = sorted(r["elapsed_seconds"] for r in records)
    return {
        "sample_count": len(values),
        "p50_seconds": round(statistics.median(values), 4) if values else None,
        "p95_seconds": values[math.ceil(len(values) * 0.95) - 1] if values else None,
        "p95_method": "nearest_rank",
    }


def summarize(records, cases, repeats):
    planned = len(cases) * repeats
    positives = [r for r in records if r["expected_status"] == "ready"]
    planned_positive = sum(c["expected_status"] == "ready" for c in cases) * repeats
    ready = [r for r in records if r["actual_status"] == "ready"]
    return {
        "planned_runs": planned,
        "completed_runs": len(records),
        "not_run": planned - len(records),
        "status_match": rate(sum(r["status_match"] for r in records), planned),
        "candidate_success": rate(sum(r["candidate_success"] for r in records), planned),
        "first_attempt_candidate_success": rate(
            sum(r["candidate_success"] and r["attempt_count"] == 1 for r in records), planned
        ),
        "ready_positive": rate(sum(r["actual_status"] == "ready" for r in positives), planned_positive),
        "core_technical_positive": rate(sum(r["technical_pass"] for r in positives), planned_positive),
        "critical_named_properties_positive": rate(
            sum(r["semantic_named_properties_pass"] for r in positives), planned_positive
        ),
        "clarification_correct": rate(
            sum(r["candidate_success"] for r in records if r["expected_status"] == "needs_clarification"),
            planned - planned_positive,
        ),
        "repair_runs": sum(r["attempt_count"] > 1 for r in records),
        "repair_attempts": sum(max(0, r["attempt_count"] - 1) for r in records),
        "error_runs": sum(r["actual_status"] == "error" for r in records),
        "metadata_captured": rate(sum(r["metadata_captured"] for r in records), planned),
        "behavior_counts": {
            s: sum(r["behavior"] == s for r in positives)
            for s in ("passed", "failed", "inconclusive", "not_run")
        },
        "timing_all_completed_including_failures": timing(records),
        "timing_ready_only": timing(ready),
        "groups": {
            group: {
                "completed": sum(r["group"] == group for r in records),
                "candidate_success": sum(r["group"] == group and r["candidate_success"] for r in records),
            }
            for group in sorted({c["group"] for c in cases})
        },
        "expert_semantic_review": "NOT_REVIEWED",
        "limitations": [
            "Конкретные свойства размечены до генераций и проверяются по XML, а не по устаревшим ir.nodes.",
            "Два повтора на описание дают небольшой контроль повторяемости, а не статистическую гарантию.",
            "Новые домены отсутствуют в full_bpmn; корпус авторский, а не внешний слепой тест.",
            "Импорт в браузере, визуальное качество и полный бизнес-смысл проверяются отдельно.",
            "Все запланированные запросы остаются в знаменателе; не выполненные показаны явно.",
        ],
        "runs": records,
    }


def source_files():
    paths = [*ROOT.glob("backend/**/*.py"), *ROOT.glob("worker/**/*.py"), *ROOT.glob("backend/**/*.xsd")]
    paths += [
        ROOT / "scripts/document_benchmark.py",
        ROOT / "scripts/quality_benchmark.py",
        ROOT / "examples/document_benchmark.json",
    ]
    return sorted(path for path in paths if path.is_file())


def source_hashes():
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files()
    }


def run(cases, repeats, output, url):
    if output.exists():
        raise ValueError(
            "Output directory already exists: preserve previous evidence and choose a new run ID"
        )
    output.mkdir(parents=True)
    frozen = source_hashes()
    _write_json(output / "cases.json", {"cases": cases})
    _write_json(output / "source-hashes.json", frozen)
    with zipfile.ZipFile(output / "source-snapshot.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files():
            archive.write(path, path.relative_to(ROOT))
    records = []
    run_info = {
        "created_at": datetime.now(UTC).isoformat(),
        "repeats": repeats,
        "planned_runs": len(cases) * repeats,
        "corpus_sha256": hashlib.sha256(
            json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "source_hashes_sha256": hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest(),
        "mode": "live",
        "transport": "authenticated HTTP API -> provider -> isolated worker",
        "authentication_saved": False,
        "order": "round-robin by repetition",
        "api_url": url,
    }
    _write_json(output / "run.json", run_info)
    images = subprocess.run(
        ["docker", "compose", "images", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if images.returncode == 0:
        (output / "compose-images.json").write_text(images.stdout, encoding="utf-8")
    with httpx.Client(base_url=url, timeout=httpx.Timeout(430, connect=10), trust_env=False) as client:
        health_response = client.get("/api/health")
        health_response.raise_for_status()
        health = health_response.json()
        _write_json(output / "health.json", health)
        if health.get("mode") != "live" or not health.get("generation_available"):
            raise RuntimeError("Live API with isolated worker required")
        username, password = provision_local_account(url)
        login = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"X-Requested-With": "ProcessArchitect"},
        )
        password = None
        login.raise_for_status()
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        schema = client.get("/api/bpmn/schema")
        schema.raise_for_status()
        _write_json(output / "schema.json", schema.json())
        last_start = 0.0
        _write_json(output / "summary.json", summarize(records, cases, repeats))
        try:
            for repeat in range(1, repeats + 1):
                for case in cases:
                    if source_hashes() != frozen:
                        raise RuntimeError(
                            "Application or benchmark source changed during frozen run; "
                            "stopping without discarding any result"
                        )
                    time.sleep(max(0, 5.1 - (time.monotonic() - last_start)))
                    directory = output / f"{case['id']}-{repeat}"
                    directory.mkdir()
                    (directory / "source.txt").write_text(case["text"], encoding="utf-8")
                    _write_json(directory / "request.json", {"description": case["text"]})
                    last_start = time.monotonic()
                    http_status = None
                    try:
                        received = client.post("/api/generate", json={"description": case["text"]})
                        http_status = received.status_code
                        response = received.json()
                        if not isinstance(response, dict):
                            raise ValueError("JSON object required")
                    except (httpx.HTTPError, ValueError):
                        response = {
                            "status": "error",
                            "issues": [
                                {
                                    "code": "BENCHMARK_TRANSPORT",
                                    "severity": "error",
                                    "message": "HTTP API response unavailable or invalid",
                                }
                            ],
                        }
                    elapsed = time.monotonic() - last_start
                    _write_json(directory / "response.json", response)
                    for key, filename in (("python_code", "generated.py"), ("bpmn_xml", "generated.bpmn")):
                        if response.get(key):
                            (directory / filename).write_text(response[key], encoding="utf-8")
                    if response.get("ir"):
                        _write_json(directory / "process_ir.json", response["ir"])
                    metadata = None
                    identifier = response.get("id", "")
                    if re.fullmatch(r"[0-9a-f]{32}", identifier):
                        try:
                            artifact = client.get(f"/api/generations/{identifier}/artifacts/metadata")
                            artifact.raise_for_status()
                            metadata = artifact.json()
                            _write_json(directory / "metadata.json", metadata)
                        except (httpx.HTTPError, ValueError):
                            _write_json(directory / "metadata-error.json", {"status": "unavailable"})
                    record = evaluate(case, response, elapsed, metadata)
                    record.update(
                        {"repeat": repeat, "http_status": http_status, "artifact_directory": directory.name}
                    )
                    if http_status != 200:
                        record["candidate_success"] = False
                    _write_json(directory / "evaluation.json", record)
                    _write_json(
                        directory / "complete.json",
                        {
                            "files": {
                                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in directory.iterdir()
                                if p.is_file()
                            }
                        },
                    )
                    records.append(record)
                    _write_json(output / "summary.json", summarize(records, cases, repeats))
                    print(
                        json.dumps(
                            {
                                "completed": len(records),
                                "total": len(cases) * repeats,
                                "case": case["id"],
                                "repeat": repeat,
                                "status": record["actual_status"],
                                "technical": record["technical_pass"],
                                "semantic": record["semantic_named_properties_pass"],
                                "attempts": record["attempt_count"],
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
            summary = summarize(records, cases, repeats)
            summary["source_unchanged_at_end"] = source_hashes() == frozen
            _write_json(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--url", default="http://127.0.0.1:8088")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("repeats must be 1..20")
    cases = json.loads((ROOT / "examples/document_benchmark.json").read_text(encoding="utf-8"))["cases"]
    output = args.output or ROOT / "artifacts/full-bpmn/benchmark" / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    summary = run(cases, args.repeats, output, args.url)
    return 0 if summary["candidate_success"]["numerator"] == summary["planned_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
