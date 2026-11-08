"""Publication guards use synthetic files; they do not claim video playback."""

import hashlib
import json
import sys
import zipfile

import pytest

from scripts import release_package as package


def reviewed_take(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "ROOT", tmp_path)
    take = tmp_path / "artifacts/quality/video-v7/live-test"
    take.mkdir(parents=True)
    release = tmp_path / "release/Process-Architect-AI-demo.mp4"
    release.parent.mkdir()
    candidate = take / "candidate.mp4"
    candidate.write_bytes(b"synthetic-media-for-hash-check-only")
    release.write_bytes(candidate.read_bytes())
    for name in (
        "source.txt", "generated.py", "process_ir.json", "metadata.json", "generated.bpmn",
        "theatre-current.bpmn", "theatre-public-edited.bpmn",
    ):
        (take / name).write_text("{}" if name.endswith(".json") else "synthetic", encoding="utf-8")
    recording = {
        "status": "RECORDED", "mode": "live", "reference_used": False, "live_requests": 1,
        "page_errors": [], "public_editor": {"status": "PASS"},
        "source_sha256": package.digest(take / "source.txt"),
        "chapters": [{"id": "intro", "start_seconds": 0, "end_seconds": 30,
                      "audio_file": "D:/private-local/raw.wav"}],
    }
    recording_path = take / "recording.json"
    recording_path.write_text(json.dumps(recording), encoding="utf-8")
    report = {
        "status": "PASS", "timeline_covered": True, "audio_transcription_checked": True,
        "human_playback_review": False, "review_methods": ["uniform frames", "ASR", "decode"],
        "frame_sampling_interval_seconds": 10, "file": candidate.relative_to(tmp_path).as_posix(),
        "sha256": package.digest(candidate), "duration_seconds": 350,
        "width": 1920, "height": 1080, "audio_codec": "aac",
        "source_recording": recording_path.relative_to(tmp_path).as_posix(),
        "source_recording_sha256": package.digest(recording_path),
    }
    report_path = take / "final-review.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return take, release, report_path, report


def test_new_video_is_allowlisted_and_raw_report_paths_are_not_published(tmp_path, monkeypatch):
    take, release, report_path, _ = reviewed_take(tmp_path, monkeypatch)
    for name in ("raw.webm", "audio/intro.wav", "model-cache/model.safetensors", ".env"):
        path = take / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"must not enter package")
    result = package.collect_video_evidence(report_path.relative_to(tmp_path).as_posix(), release)
    assert len(result["files"]) == 9
    assert all("must not enter package" not in data.decode() for data in result["files"].values())
    projected = next(data for name, data in result["files"].items() if name.endswith("recording-public.json"))
    assert b"private-local" not in projected and b"raw.wav" not in projected
    assert json.loads(projected)["original_report_sha256"] == package.digest(take / "recording.json")
    assert result["review"]["human_playback_review"] is False


@pytest.mark.parametrize("changed", ["candidate", "release", "recording"])
def test_changed_reviewed_media_or_recording_is_rejected(tmp_path, monkeypatch, changed):
    take, release, report_path, _ = reviewed_take(tmp_path, monkeypatch)
    target = {"candidate": take / "candidate.mp4", "release": release,
              "recording": take / "recording.json"}[changed]
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256|changed after video review"):
        package.collect_video_evidence(report_path.relative_to(tmp_path).as_posix(), release)


@pytest.mark.parametrize("unsafe", [
    "../../local_data/private.mp4", "local_data/private.mp4", "D:/private/candidate.mp4",
    "artifacts/quality/video-v7/another-take/candidate.mp4",
])
def test_candidate_cannot_escape_selected_take(tmp_path, monkeypatch, unsafe):
    _, release, report_path, report = reviewed_take(tmp_path, monkeypatch)
    other = tmp_path / "artifacts/quality/video-v7/another-take/candidate.mp4"
    other.parent.mkdir()
    other.write_bytes(release.read_bytes())
    report["file"] = unsafe
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        package.collect_video_evidence(report_path.relative_to(tmp_path).as_posix(), release)


@pytest.mark.parametrize("unsafe_content", ["secret", "absolute_path"])
def test_evidence_rejects_configured_secret_or_private_path(tmp_path, monkeypatch, unsafe_content):
    take, release, report_path, _ = reviewed_take(tmp_path, monkeypatch)
    if unsafe_content == "secret":
        private = tmp_path / "local_data"
        private.mkdir()
        (private / "gitlab-pat.txt").write_text("synthetic-sensitive-value", encoding="utf-8")
        content = {"note": "synthetic-sensitive-value"}
    else:
        content = {"file": "C:/Users/private/model-cache/weight.bin"}
    (take / "metadata.json").write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match="value not printed") as error:
        package.collect_video_evidence(report_path.relative_to(tmp_path).as_posix(), release)
    assert "synthetic-sensitive-value" not in str(error.value)
    assert "Users/private" not in str(error.value)


def test_technical_validation_does_not_count_as_final_video_review(tmp_path, monkeypatch):
    _, release, report_path, report = reviewed_take(tmp_path, monkeypatch)
    report["status"] = "TECHNICAL_PASS_AWAITING_FULL_REVIEW"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="completed timeline"):
        package.collect_video_evidence(report_path.relative_to(tmp_path).as_posix(), release)


def test_optional_tts_lock_is_included_and_nested_cache_is_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "ROOT", tmp_path)
    for name in (
        "requirements-tts.windows-cu128.txt", "scripts/local_tts_preview.py",
        "scripts/.venv-tts/private.py", "scripts/model-cache/config.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic", encoding="utf-8")
    assert {path.relative_to(tmp_path).as_posix() for path in package.safe_sources()} == {
        "requirements-tts.windows-cu128.txt", "scripts/local_tts_preview.py",
    }


@pytest.mark.parametrize("new_video", [False, True])
def test_archive_manifest_matches_contents_and_legacy_cli_still_works(tmp_path, monkeypatch, new_video):
    _, _, review_path, _ = reviewed_take(tmp_path, monkeypatch)
    (tmp_path / "release/Process-Architect-AI.pdf").write_bytes(b"synthetic-pdf-for-packaging")
    for name, data in (
        ("artifacts/quality/benchmark/run-02/summary.json", {"not_run": 0}),
        ("artifacts/clean-install/report.json", {"commands": []}),
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    arguments = ["release_package"]
    if new_video:
        arguments += ["--video-report", review_path.relative_to(tmp_path).as_posix()]
    monkeypatch.setattr(sys, "argv", arguments)
    package.package()
    with zipfile.ZipFile(tmp_path / "release/Process-Architect-AI-source.zip") as archive:
        manifest = json.loads(archive.read("RELEASE_MANIFEST.json"))
        packaged_paths = set(archive.namelist()) - {"RELEASE_MANIFEST.json"}
        assert {row["path"] for row in manifest["files"]} == packaged_paths
        for row in manifest["files"]:
            data = archive.read(row["path"])
            assert row["sha256"] == hashlib.sha256(data).hexdigest()
            assert row["bytes"] == len(data)
        if new_video:
            assert manifest["video_review"]["human_playback_review"] is False
            assert any(name.endswith("recording-public.json") for name in archive.namelist())
            assert not any(name.endswith("recording.json") for name in archive.namelist())
        else:
            assert manifest["video_scope"].startswith("Earlier prototype")
            assert "video_review" not in manifest
