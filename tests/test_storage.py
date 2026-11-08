import json
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from backend.app.domain import PipelineError
from backend.app.storage import KINDS, Storage


def generation():
    return {
        "id": uuid.uuid4().hex,
        "revision_id": uuid.uuid4().hex,
        "mode": "mock",
        "status": "ready",
        "python_code": 'bpmn.process(id="p1", name="Заявка")',
        "ir": {"processes": [{"id": "p1", "name": "Заявка"}]},
        "bpmn_xml": '<definitions name="Исходная схема"/>',
        "checks": {
            "code": "passed",
            "xsd": "passed",
            "graph": "passed",
            "layout": "passed",
            "import": "not_run",
        },
        "issues": [
            {"code": "MODEL_NOTE", "category": "model", "severity": "warning", "message": "Проверьте роль"}
        ],
    }


def validation(marker="CURRENT"):
    return {
        "checks": {"xsd": "passed", "graph": "passed", "layout": "passed", "import": "not_run"},
        "issues": [{"code": marker, "category": "technical", "severity": "info", "message": "Проверено"}],
    }


@pytest.fixture
def saved(tmp_path):
    storage, result = Storage(tmp_path / "data"), generation()
    storage.save(result, "Менеджер проверяет заявку.", {"mode": "mock", "model": None})
    return storage, result


def assert_code(code, operation):
    with pytest.raises(PipelineError) as caught:
        operation()
    assert caught.value.issue.code == code


def test_generation_artifacts_are_immutable_and_current_xml_is_exact(saved):
    storage, initial = saved
    before = {kind: storage.artifact(initial["id"], kind).read_bytes() for kind in KINDS}
    xml = '<definitions name="Ручная правка"><Bounds x="721.5" y="88"/></definitions>'
    report = validation()
    original_report = deepcopy(report)
    revision = storage.revise(initial["id"], initial["revision_id"], xml, report)
    report["checks"]["xsd"] = "failed"
    fetched = storage.get(initial["id"])
    assert fetched["bpmn_xml"] == xml
    assert fetched["python_code"] == initial["python_code"]
    assert fetched["ir"] == initial["ir"]
    assert fetched["issues"] == initial["issues"]
    assert fetched["revision_issues"] == original_report["issues"]
    assert fetched["checks"] == original_report["checks"]
    assert fetched["audit_stale"] is True
    assert fetched["revision_id"] == revision["revision_id"] != initial["revision_id"]
    assert {kind: storage.artifact(initial["id"], kind).read_bytes() for kind in KINDS} == before
    revision_metadata = storage.directory(initial["id"]) / "revisions" / f"{revision['revision_id']}.json"
    stored = json.loads(revision_metadata.read_text("utf-8"))
    assert stored["base_revision_id"] == initial["revision_id"]
    assert stored["checks"] == original_report["checks"]
    assert stored["created_at"]


def test_source_code_and_xml_preserve_mixed_newlines_byte_exactly(tmp_path):
    storage, result = Storage(tmp_path / "data"), generation()
    source = "Русская строка\r\nВторая строка\nТретья\rПоследняя\n"
    code = 'bpmn.process(id="p1", name="Процесс")\r\n# строка\n# ещё\r'
    xml = '<definitions>\n<process name="Процесс"/>\r\n</definitions>\r'
    result.update(python_code=code, bpmn_xml=xml)
    storage.save(result, source, {})
    for kind, original in [("source", source), ("python", code), ("xml", xml)]:
        assert storage.artifact(result["id"], kind).read_bytes() == original.encode("utf-8")
    assert storage.get(result["id"])["bpmn_xml"] == xml
    edited = '<definitions>\r\n<process name="Правка"/>\n</definitions>\r'
    revision = storage.revise(result["id"], result["revision_id"], edited, validation())
    revision_file = storage.directory(result["id"]) / "revisions" / f"{revision['revision_id']}.bpmn"
    assert revision_file.read_bytes() == edited.encode("utf-8")
    assert storage.get(result["id"])["bpmn_xml"] == edited
    assert storage.artifact(result["id"], "xml").read_bytes() == xml.encode("utf-8")


def test_revisions_keep_each_validation_report_and_reject_stale_base(saved):
    storage, initial = saved
    first = storage.revise(initial["id"], initial["revision_id"], "first", validation("FIRST"))
    second = storage.revise(initial["id"], first["revision_id"], "second", validation("SECOND"))
    assert_code(
        "REVISION_CONFLICT",
        lambda: storage.revise(initial["id"], initial["revision_id"], "lost", validation()),
    )
    assert storage.get(initial["id"])["bpmn_xml"] == "second"
    assert storage.get(initial["id"])["revision_id"] == second["revision_id"]
    path = storage.directory(initial["id"]) / "revisions"
    assert (path / f"{first['revision_id']}.bpmn").read_text("utf-8") == "first"
    assert (
        json.loads((path / f"{first['revision_id']}.json").read_text("utf-8"))["issues"][0]["code"] == "FIRST"
    )


def test_resaving_generation_cannot_overwrite_original(saved):
    storage, result = saved
    modified = {**result, "python_code": "different"}
    assert_code("GENERATION_EXISTS", lambda: storage.save(modified, "different", {}))
    assert storage.get(result["id"])["python_code"] == result["python_code"]


@pytest.mark.parametrize(
    "value", ["../outside", "../../../../windows", "C:\\Windows", "a" * 31, "a" * 33, None]
)
def test_generation_and_save_ids_cannot_escape_data_root(tmp_path, value):
    storage, result = Storage(tmp_path / "data"), generation()
    result["id"] = value
    assert_code("INVALID_GENERATION_ID", lambda: storage.save(result, "source", {}))
    assert_code("NOT_FOUND", lambda: storage.get(value))
    assert_code("NOT_FOUND", lambda: storage.artifact(value, "source"))
    assert not list(storage.root.glob(".pending_*"))


@pytest.mark.parametrize("kind", ["../metadata.json", "revisions/foo.bpmn", "result", "head", "source.txt"])
def test_artifact_kind_is_allowlisted(saved, kind):
    storage, result = saved
    assert_code("NOT_FOUND", lambda: storage.artifact(result["id"], kind))


def test_invalid_revision_id_in_head_is_not_a_filesystem_path(saved):
    storage, result = saved
    head_path = storage.directory(result["id"]) / "head.json"
    head_path.write_text(json.dumps({"revision_id": "../../outside", "audit_stale": True}), encoding="utf-8")
    assert_code("STORAGE_CORRUPT", lambda: storage.get(result["id"]))


def test_validation_cannot_replace_server_revision_identity(saved):
    storage, result = saved
    report = {**validation(), "revision_id": "../../outside", "audit_stale": False}
    revision = storage.revise(result["id"], result["revision_id"], "edited", report)
    assert len(revision["revision_id"]) == 32
    assert revision["audit_stale"] is True
    assert storage.get(result["id"])["bpmn_xml"] == "edited"


def test_no_revision_can_be_created_for_generation_without_xml(tmp_path):
    storage, result = Storage(tmp_path / "data"), generation()
    result.update(status="needs_clarification", python_code=None, ir=None, bpmn_xml=None)
    storage.save(result, "Неизвестный исполнитель", {})
    assert storage.get(result["id"])["bpmn_xml"] is None
    assert_code(
        "REVISION_UNAVAILABLE",
        lambda: storage.revise(result["id"], result["revision_id"], "new", validation()),
    )
    assert_code("NOT_FOUND", lambda: storage.artifact(result["id"], "xml"))


def test_interrupted_generation_publication_leaves_no_partial_generation(tmp_path, monkeypatch):
    storage, result = Storage(tmp_path / "data"), generation()
    real_json = storage._json

    def failing_json(path, data):
        if path.name == "head.json":
            raise OSError("Simulated disk failure")
        real_json(path, data)

    monkeypatch.setattr(storage, "_json", failing_json)
    with pytest.raises(OSError):
        storage.save(result, "source", {})
    assert_code("NOT_FOUND", lambda: storage.get(result["id"]))
    assert not list(storage.root.glob(".pending_*"))


def test_interrupted_head_publication_keeps_previous_revision(saved, monkeypatch):
    storage, result = saved
    real_json = storage._json

    def failing_head(path, data):
        if path.name == "head.json":
            raise OSError("Simulated disk failure")
        real_json(path, data)

    monkeypatch.setattr(storage, "_json", failing_head)
    with pytest.raises(OSError):
        storage.revise(result["id"], result["revision_id"], "unpublished", validation())
    current = storage.get(result["id"])
    assert current["revision_id"] == result["revision_id"]
    assert current["bpmn_xml"] == result["bpmn_xml"]


def test_concurrent_storage_instances_allow_only_one_update(saved):
    storage, result = saved
    barrier = threading.Barrier(2)

    def update(index):
        independent = Storage(storage.root)
        original_head = independent._head

        def delayed_head(path):
            head = original_head(path)
            time.sleep(0.03)
            return head

        independent._head = delayed_head
        barrier.wait(timeout=2)
        try:
            independent.revise(result["id"], result["revision_id"], f"edited {index}", validation())
            return "saved"
        except PipelineError as error:
            return error.issue.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(update, range(2)))
    assert sorted(outcomes) == ["REVISION_CONFLICT", "saved"]
    assert len(list((storage.directory(result["id"]) / "revisions").glob("*.bpmn"))) == 1


def test_separate_processes_share_revision_lock(saved):
    storage, result = saved
    script = """
import sys, time
from pathlib import Path
from backend.app.storage import Storage
from backend.app.domain import PipelineError
store = Storage(Path(sys.argv[1]))
original = store._head
def delayed(path):
    head = original(path)
    time.sleep(0.1)
    return head
store._head = delayed
try:
    store.revise(sys.argv[2], sys.argv[3], 'child edit', {'checks': {'xsd': 'passed'}, 'issues': []})
    print('saved')
except PipelineError as error:
    print(error.issue.code)
"""
    arguments = [sys.executable, "-c", script, str(storage.root), result["id"], result["revision_id"]]
    children = [
        subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        for _ in range(2)
    ]
    outputs = []
    try:
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, stderr
            outputs.append(stdout.strip())
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    assert sorted(outputs) == ["REVISION_CONFLICT", "saved"]


def symlink(path, destination, directory=False):
    try:
        path.symlink_to(destination, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"Системная политика не позволяет создать symlink для проверки: {error}")


def test_artifact_symlink_is_rejected(saved, tmp_path):
    storage, result = saved
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    artifact = storage.artifact(result["id"], "source")
    artifact.unlink()
    symlink(artifact, outside)
    assert_code("NOT_FOUND", lambda: storage.artifact(result["id"], "source"))


def test_generation_directory_symlink_is_rejected(saved):
    storage, result = saved
    another_id = uuid.uuid4().hex
    symlink(storage.root / another_id, storage.directory(result["id"]), directory=True)
    assert_code("NOT_FOUND", lambda: storage.get(another_id))


def test_revisions_directory_symlink_cannot_receive_output(saved, tmp_path):
    storage, result = saved
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink(storage.directory(result["id"]) / "revisions", outside, directory=True)
    assert_code(
        "NOT_FOUND", lambda: storage.revise(result["id"], result["revision_id"], "leak", validation())
    )
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("filename", ["head.json", "result.json"])
def test_metadata_symlink_cannot_be_read(saved, tmp_path, filename):
    storage, result = saved
    original = storage.directory(result["id"]) / filename
    outside = tmp_path / filename
    outside.write_bytes(original.read_bytes())
    original.unlink()
    symlink(original, outside)
    assert_code("NOT_FOUND", lambda: storage.get(result["id"]))
