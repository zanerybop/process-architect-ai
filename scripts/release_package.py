"""Create a local source/evidence handoff; never includes local accounts or credentials."""

import argparse
import hashlib
import json
import os
import re
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {
    "backend", "config", "docs", "examples", "frontend", "prompts", "references",
    "reports", "schemas", "scripts", "tests", "worker",
}
ROOT_FILES = {
    "README.md", "AGENTS.md", "CODEX_TASK.md", "compose.yaml", "pyproject.toml", "uv.lock",
    "requirements.lock", "requirements-tts.windows-cu128.txt", ".env.example", ".gitattributes",
    ".gitignore", ".dockerignore", ".python-version",
}
EXCLUDED = {
    "node_modules", "dist", "__pycache__", ".venv", ".pytest_cache", ".ruff_cache",
    "test-results", "playwright-report", "local_data", ".git", ".venv-tts", "model-cache",
    "raw-chunks",
}


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_sources(source_root=None):
    source_root = ROOT if source_root is None else source_root
    for name in sorted(ROOT_FILES):
        path = source_root / name
        if path.is_file() and not is_link(path):
            yield path
    for name in sorted(SOURCE_DIRS):
        for directory, names, files in os.walk(source_root / name, followlinks=False):
            parent = Path(directory)
            names[:] = [item for item in names if item not in EXCLUDED and not is_link(parent / item)]
            for item in sorted(files):
                path = parent / item
                if is_link(path) or (item.startswith(".env") and item != ".env.example"):
                    continue
                if path.suffix.lower() in {
                    ".db", ".sqlite3", ".pem", ".key", ".log", ".pyc", ".tsbuildinfo",
                    ".safetensors", ".gguf", ".wav", ".mp3", ".webm", ".mp4",
                }:
                    continue
                yield path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _video_path(value, *, parent=None):
    """Only existing non-linked paths in the explicitly selected synthetic take."""
    if (not isinstance(value, str) or not value
            or Path(value).is_absolute() or PureWindowsPath(value).is_absolute()):
        raise ValueError("Video evidence paths must be repository-relative")
    lexical = Path(value.replace("\\", "/"))
    if ".." in lexical.parts:
        raise ValueError("Video evidence path traversal is forbidden")
    path = ROOT / lexical
    video_root = (ROOT / "artifacts/quality/video-v7").resolve()
    if not path.resolve().is_relative_to(video_root) or not path.is_file():
        raise ValueError("Video evidence must exist inside artifacts/quality/video-v7")
    if any(is_link(item) for item in [path, *path.parents] if item != ROOT and item.is_relative_to(ROOT)):
        raise ValueError("Linked video evidence is forbidden")
    if parent is not None and path.parent.resolve() != parent.resolve():
        raise ValueError("Video evidence must belong to the same recorded take")
    return path


def _configured_secrets():
    values = []
    pat = ROOT / "local_data/gitlab-pat.txt"
    if pat.is_file():
        value = pat.read_text(encoding="utf-8-sig").strip()
        if value:
            values.append(value.encode())
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("\"'")
            if key == "LLM_API_KEY" and value == "local-strata":
                continue  # Documented placeholder for the unauthenticated local server.
            if re.search(r"(?:SECRET|PASSWORD|TOKEN|API_KEY)$", key) and len(value) >= 8:
                values.append(value.encode())
    return values


def _public_video_bytes(data, secret_values):
    if any(secret in data for secret in secret_values):
        raise ValueError("Configured secret found in video evidence; value not printed")
    text = data.decode("utf-8")
    if re.search(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|/(?:Users|home|root|tmp)/|\\\\[^\\\s]+\\)", text):
        raise ValueError("Absolute local path found in video evidence; value not printed")
    return data


def collect_video_evidence(report_name, release_video):
    """Bind the reviewed MP4 to its live take; publish only a compact allowlist.

    This verifies recorded review evidence, not human playback. Raw recording and
    audio manifests remain local; derived reports explicitly identify projections.
    """
    report_path = _video_path(report_name)
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    if (
        report.get("status") != "PASS" or report.get("timeline_covered") is not True
        or report.get("audio_transcription_checked") is not True
        or not isinstance(report.get("human_playback_review"), bool)
        or not isinstance(report.get("review_methods"), list) or not report["review_methods"]
        or not all(isinstance(item, str) and item for item in report["review_methods"])
        or not isinstance(report.get("frame_sampling_interval_seconds"), (int, float))
        or report["frame_sampling_interval_seconds"] <= 0
    ):
        raise ValueError("Video needs a completed timeline, transcription and documented review")
    candidate = _video_path(report.get("file"), parent=report_path.parent)
    if (
        candidate.suffix.lower() != ".mp4" or not 0 < candidate.stat().st_size <= 200_000_000
        or digest(candidate) != report.get("sha256") or digest(release_video) != report["sha256"]
    ):
        raise ValueError("Reviewed candidate and release MP4 must have the exact reviewed SHA-256")
    if (
        not 0 < float(report.get("duration_seconds", 0)) <= 420
        or report.get("width", 0) < 1280 or report.get("height", 0) < 720
        or not report.get("audio_codec")
    ):
        raise ValueError("Reviewed video must meet the duration, HD and audio requirements")
    recording_path = _video_path(report.get("source_recording"), parent=report_path.parent)
    if digest(recording_path) != report.get("source_recording_sha256"):
        raise ValueError("Recording report changed after video review")
    recording = json.loads(recording_path.read_text(encoding="utf-8-sig"))
    if (
        recording.get("status") != "RECORDED" or recording.get("mode") != "live"
        or recording.get("reference_used") is not False or recording.get("live_requests") != 1
        or recording.get("page_errors") != [] or recording.get("public_editor", {}).get("status") != "PASS"
    ):
        raise ValueError("Only a successful live take with external editor verification can be released")
    secret_values = _configured_secrets()
    public_files = {}
    for name in (
        "source.txt", "generated.py", "process_ir.json", "metadata.json", "generated.bpmn",
        "theatre-current.bpmn", "theatre-public-edited.bpmn",
    ):
        path = _video_path(
            (report_path.parent / name).relative_to(ROOT).as_posix(), parent=report_path.parent,
        )
        public_files[path.relative_to(ROOT).as_posix()] = _public_video_bytes(
            path.read_bytes(), secret_values,
        )
    source = public_files[(report_path.parent / "source.txt").relative_to(ROOT).as_posix()]
    if hashlib.sha256(source).hexdigest() != recording.get("source_sha256"):
        raise ValueError("Recorded source changed after the live request")
    # Original reports can contain absolute audio paths; keep their hashes and
    # explicitly publish only non-sensitive fields instead of mutating the originals.
    review_keys = (
        "status", "file", "sha256", "duration_seconds", "width", "height", "audio_codec",
        "source_recording", "source_recording_sha256", "review_methods",
        "frame_sampling_interval_seconds", "timeline_covered", "audio_transcription_checked",
        "human_playback_review",
    )
    reviewed = {key: report[key] for key in review_keys}
    recording_keys = (
        "status", "mode", "source", "source_sha256", "reference_used", "started_at", "live_requests",
        "page_errors", "base_url", "narration_sha256", "generation", "duration_seconds",
        "app_semantic_preservation", "public_semantic_preservation", "public_semantic_review",
        "public_export_validation", "public_editor", "visible_checks", "displayed_source_quote",
    )
    public_recording = {key: recording[key] for key in recording_keys if key in recording}
    chapter_keys = ("id", "title", "start_seconds", "end_seconds", "audio_seconds")
    public_recording["chapters"] = [
        {key: chapter[key] for key in chapter_keys if key in chapter}
        for chapter in recording.get("chapters", [])
    ]
    for name, data, original in (
        ("review-public.json", reviewed, report_path),
        ("recording-public.json", public_recording, recording_path),
    ):
        data = {"projection": "Public fields only; original local report is unchanged",
                "original_report_sha256": digest(original), **data}
        public_files[(report_path.parent / name).relative_to(ROOT).as_posix()] = _public_video_bytes(
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(), secret_values,
        )
    return {
        "scope": (
            "Live v7 theatre process: compensation, current XML edit/export "
            "and public bpmn.io edit/reimport."
        ),
        "review": reviewed,
        "files": public_files,
    }


def package():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default="artifacts/quality/benchmark/run-02")
    parser.add_argument("--browser", default="artifacts/quality/benchmark/browser-02")
    parser.add_argument("--video-report", help="Repository-relative final review of the new live v7 video")
    parser.add_argument(
        "--source-root", help="Reviewed local source snapshot; evidence still comes from this workspace",
    )
    args = parser.parse_args()
    source_root = (ROOT / args.source_root).resolve() if args.source_root else ROOT
    if not source_root.is_dir() or not source_root.is_relative_to(ROOT):
        parser.error("Source snapshot must be an existing directory inside this workspace.")
    benchmark = (ROOT / args.benchmark).resolve()
    browser = (ROOT / args.browser).resolve()
    document_release = benchmark.is_relative_to(ROOT / "artifacts/full-bpmn")
    for target in (benchmark, browser):
        if not any(target.is_relative_to(ROOT / prefix) for prefix in (
            "artifacts/quality", "artifacts/full-bpmn",
        )):
            parser.error("Evidence must be inside artifacts/quality or artifacts/full-bpmn.")
    summary = json.loads((benchmark / "summary.json").read_text(encoding="utf-8"))
    if summary["not_run"] != 0:
        parser.error("The final benchmark must be complete; failed results stay in the package.")
    output = ROOT / "release"
    output.mkdir(exist_ok=True)
    pdf = output / "Process-Architect-AI.pdf"
    videos = list(output.glob("*.mp4"))
    if not pdf.is_file() or pdf.stat().st_size > 20_000_000 or len(videos) != 1:
        parser.error("Expected one PDF up to 20 MB and one MP4.")
    if videos[0].stat().st_size > 200_000_000:
        parser.error("The video exceeds 200 MB.")
    video_evidence = None
    if args.video_report:
        try:
            video_evidence = collect_video_evidence(args.video_report, videos[0])
        except (ValueError, OSError, TypeError) as exc:
            parser.error(str(exc))
    if document_release:
        browser_report = json.loads((browser / "report.json").read_text(encoding="utf-8"))
        ready_ids = {
            row["artifact_directory"] for row in summary["runs"] if row["actual_status"] == "ready"
        }
        reviewed = [
            row for row in browser_report["cases"]
            if row["origin"] in {"saved_model_output_recompiled", "current_benchmark_live_output"}
        ]
        if (
            browser_report["status"] != "PASS"
            or any(row["status"] != "PASS" for row in browser_report["cases"])
            or {Path(row["input"]).parent.name for row in reviewed} != ready_ids
        ):
            parser.error("Every ready benchmark output must have a successful browser roundtrip.")
        for row in reviewed:
            path = (ROOT / row["input"]).resolve()
            if not path.is_relative_to(ROOT / "artifacts/full-bpmn"):
                parser.error("Browser evidence points outside the document artifact directory.")
            if digest(path) != row["input_sha256"]:
                parser.error("A browser-tested BPMN file changed after its check.")
        deck_report = json.loads(
            (ROOT / "artifacts/quality/presentation-v3/report.json").read_text(encoding="utf-8")
        )
        if (
            deck_report["status"] != "PASS" or deck_report["sha256"] != digest(pdf)
            or (ROOT / deck_report["benchmark_report"]).resolve() != benchmark / "summary.json"
        ):
            parser.error("The PDF must match the verified current presentation and benchmark.")
    files = {path.relative_to(source_root).as_posix(): path for path in safe_sources(source_root)}
    # These directories contain only synthetic evaluation inputs and model outputs.
    evidence_dirs = [benchmark, browser, ROOT / "artifacts/quality/benchmark/run-01"]
    holdout = ROOT / "artifacts/quality/benchmark/holdout-01"
    if holdout.exists():
        holdout_summary = json.loads((holdout / "summary.json").read_text(encoding="utf-8"))
        if holdout_summary["not_run"] != 0:
            parser.error("The holdout is incomplete; keep every result before packaging.")
        evidence_dirs.extend([holdout, ROOT / "artifacts/quality/benchmark/holdout-browser-01"])
    evidence_dirs.append(ROOT / "artifacts/quality/presentation-v2")
    if document_release:
        evidence_dirs.extend([
            ROOT / "artifacts/full-bpmn", ROOT / "artifacts/quality/presentation-v3",
            ROOT / "artifacts/full-bpmn-ui",
        ])
    for directory in evidence_dirs:
        for path in directory.rglob("*"):
            if (path.is_file() and not is_link(path)
                    and path.suffix in {".json", ".txt", ".py", ".bpmn", ".md", ".svg", ".html", ".xml"}):
                files[path.relative_to(ROOT).as_posix()] = path
    frozen_source = benchmark / "source-snapshot.zip"
    if document_release and frozen_source.is_file() and not is_link(frozen_source):
        files[frozen_source.relative_to(ROOT).as_posix()] = frozen_source
    for name in (
        "artifacts/README.md", "artifacts/quality/release-facts.json",
        "artifacts/quality/python-tests.xml", "artifacts/quality/final-python-tests.xml",
        "artifacts/quality/final-browser-tests.json",
        "artifacts/quality/v3-regression.xml", "artifacts/quality/layout/before-after.json",
        "artifacts/container/report.json", "artifacts/quality/video/media-validation.json",
        "artifacts/quality/video/recording-recovered.json", "artifacts/quality/video/metadata.json",
        "artifacts/quality/video/input.txt", "artifacts/quality/video/diagram.png",
        "artifacts/quality/independent-semantic-review.json",
        "artifacts/clean-install/report.json",
        "artifacts/clean-install/source-manifest.json",
        "artifacts/quality/source-freeze-audit.json",
        "artifacts/quality/holdout-freeze.json", "artifacts/quality/holdout-methodology.md",
        "artifacts/quality/video/generated.bpmn", "artifacts/quality/video/generated.py",
        "artifacts/quality/video/ir.json", "artifacts/quality/video/generation.json",
        "artifacts/quality/video/P05-current.bpmn", "artifacts/quality/benchmark/run-01/summary.json",
        "artifacts/quality/video/P05-public-edited.bpmn",
        "artifacts/quality/document-family-prompt-tests.xml",
        "artifacts/quality/document-validation-tests.xml",
        "artifacts/quality/document-source-review-tests.xml",
    ):
        path = ROOT / name
        if path.is_file():
            files[name] = path
    clean_report = json.loads((ROOT / "artifacts/clean-install/report.json").read_text(encoding="utf-8"))
    for step in clean_report["commands"]:
        for key in ("stdout_log", "stderr_log"):
            path = ROOT / "artifacts/clean-install" / step[key]
            files[path.relative_to(ROOT).as_posix()] = path
    generated_files = video_evidence["files"] if video_evidence else {}
    manifest = {
        "created_at": datetime.now(UTC).isoformat(), "status": "working_prototype_with_documented_limits",
        "benchmark": args.benchmark,
        "browser_evidence": args.browser,
        "source_snapshot": source_root.relative_to(ROOT).as_posix(),
        "presentation_version": "document_bpmn_v3" if document_release else "legacy_profile_v2",
        "video_scope": video_evidence["scope"] if video_evidence else (
            "Earlier prototype recording; advanced document BPMN is not demonstrated in this video."
        ),
        "privacy": (
            "No .env, local_data, accounts, sessions, dependency directories or raw private processes."
        ),
        "files": [{"path": name, "bytes": path.stat().st_size, "sha256": digest(path)}
                  for name, path in sorted(files.items())],
    }
    if video_evidence:
        manifest["video_review"] = video_evidence["review"]
        manifest["files"].extend(
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(generated_files.items())
        )
    archive = output / "Process-Architect-AI-source.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=7) as package_zip:
        for name, path in sorted(files.items()):
            package_zip.write(path, name)
        for name, data in sorted(generated_files.items()):
            package_zip.writestr(name, data)
        package_zip.writestr("RELEASE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(archive) as package_zip:
        assert package_zip.testzip() is None
        assert ".env" not in package_zip.namelist()
        assert not any(name.startswith("local_data/") or "accounts.sqlite3" in name
                       for name in package_zip.namelist())
    deliverables = [pdf, videos[0], archive]
    result = {"created_at": manifest["created_at"], "source_files": len(manifest["files"]), "files": [
        {"file": path.name, "bytes": path.stat().st_size, "sha256": digest(path)} for path in deliverables
    ]}
    (output / "checksums.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    package()
