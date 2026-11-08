"""Immutable generation artifacts and optimistic-concurrency XML revisions."""

import json
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.app.domain import PipelineError

ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
KINDS = {
    "source": "source.txt",
    "python": "generated.py",
    "ir": "process_ir.json",
    "xml": "generated.bpmn",
    "checks": "checks.json",
    "metadata": "metadata.json",
}
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _valid_id(value):
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None


def _is_link(path: Path):
    """Include Windows junctions/reparse points, not only symbolic links."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


class Storage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(self.root, threading.RLock())

    def for_user(self, user_id: str):
        """A separate namespace preserves unclaimed legacy data without granting it to new accounts."""
        if not _valid_id(user_id):
            raise PipelineError("NOT_FOUND", "Пользователь не найден.")
        users = self.root / "users"
        target = users / user_id
        if _is_link(users) or _is_link(target) or target.resolve().parent != users.resolve():
            raise PipelineError("STORAGE_UNAVAILABLE", "Хранилище пользователя недоступно.")
        return Storage(target)

    def list_generations(self, limit=100):
        items = []
        with self._exclusive():
            directories = sorted(
                (path for path in self.root.iterdir()
                 if _valid_id(path.name) and path.is_dir() and not _is_link(path)),
                key=lambda path: path.stat().st_mtime, reverse=True,
            )[:limit]
            for directory in directories:
                metadata = self._read_json(directory, "metadata.json")
                head = self._head(directory)
                source = self._file(directory, "source.txt").read_text(encoding="utf-8")
                items.append({
                    "id": directory.name,
                    "revision_id": head["revision_id"],
                    "title": " ".join(source.split())[:120],
                    "status": metadata.get("status", "error"),
                    "created_at": metadata.get("created_at"),
                    "audit_stale": head["audit_stale"],
                })
        return sorted(items, key=lambda item: item["created_at"] or "", reverse=True)

    @contextmanager
    def _exclusive(self):
        """Serialize publication/head changes across instances and local API processes."""
        with self.lock:
            lock_path = self.root / ".storage.lock"
            if _is_link(lock_path):
                raise PipelineError("STORAGE_UNAVAILABLE", "Файл блокировки хранилища недоступен.")
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(lock_path, flags, 0o600)
            acquired = False
            try:
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                deadline = time.monotonic() + 5
                while not acquired:
                    try:
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except OSError as error:
                        if time.monotonic() >= deadline:
                            raise PipelineError(
                                "STORAGE_BUSY", "Хранилище занято; повторите запрос."
                            ) from error
                        time.sleep(0.01)
                yield
            finally:
                if acquired:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def directory(self, generation_id: str) -> Path:
        if not _valid_id(generation_id):
            raise PipelineError("NOT_FOUND", "Генерация не найдена.")
        target = self.root / generation_id
        if _is_link(target) or target.resolve().parent != self.root or not target.is_dir():
            raise PipelineError("NOT_FOUND", "Генерация не найдена.")
        return target

    @staticmethod
    def _file(directory: Path, filename: str) -> Path:
        path = directory / filename
        if _is_link(path) or path.resolve().parent != directory.resolve() or not path.is_file():
            raise PipelineError("NOT_FOUND", "Артефакт не найден.")
        return path

    @staticmethod
    def _json(path: Path, data):
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _text(path: Path, content: str):
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _read_json(self, directory: Path, filename: str):
        try:
            result = json.loads(self._file(directory, filename).read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (json.JSONDecodeError, UnicodeError, ValueError) as error:
            raise PipelineError("STORAGE_CORRUPT", "Метаданные хранилища повреждены.") from error

    def save(self, result: dict, description: str, metadata: dict):
        if not _valid_id(result.get("id")) or not _valid_id(result.get("revision_id")):
            raise PipelineError("INVALID_GENERATION_ID", "Некорректный идентификатор генерации или ревизии.")
        with self._exclusive():
            target = self.root / result["id"]
            if target.exists() or _is_link(target):
                raise PipelineError("GENERATION_EXISTS", "Исходные артефакты генерации неизменяемы.")
            pending = Path(tempfile.mkdtemp(prefix=".pending_", dir=self.root))
            try:
                self._text(pending / "source.txt", description)
                if result.get("python_code") is not None:
                    self._text(pending / "generated.py", result["python_code"])
                if result.get("ir") is not None:
                    self._json(pending / "process_ir.json", result["ir"])
                if result.get("bpmn_xml") is not None:
                    self._text(pending / "generated.bpmn", result["bpmn_xml"])
                self._json(pending / "checks.json", {"checks": result["checks"], "issues": result["issues"]})
                self._json(pending / "metadata.json", metadata)
                self._json(pending / "result.json", result)
                self._json(
                    pending / "head.json", {"revision_id": result["revision_id"], "audit_stale": False}
                )
                # Readers see either no generation or its complete immutable initial snapshot.
                pending.rename(target)
            finally:
                if pending.exists() and pending.resolve().parent == self.root:
                    for child in pending.iterdir():
                        if child.is_file() or child.is_symlink():
                            child.unlink()
                    pending.rmdir()

    def _head(self, target: Path):
        head = self._read_json(target, "head.json")
        if not _valid_id(head.get("revision_id")) or not isinstance(head.get("audit_stale"), bool):
            raise PipelineError("STORAGE_CORRUPT", "Метаданные текущей ревизии повреждены.")
        return head

    @staticmethod
    def _revisions(target: Path, create=False):
        directory = target / "revisions"
        if _is_link(directory) or directory.resolve().parent != target:
            raise PipelineError("NOT_FOUND", "Каталог ревизий недоступен.")
        if create:
            directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir():
            raise PipelineError("NOT_FOUND", "Ревизия не найдена.")
        return directory

    def get(self, generation_id: str) -> dict:
        with self._exclusive():
            target = self.directory(generation_id)
            result = self._read_json(target, "result.json")
            head = self._head(target)
            if result.get("id") != generation_id or not _valid_id(result.get("revision_id")):
                raise PipelineError("STORAGE_CORRUPT", "Метаданные генерации повреждены.")
            if head["revision_id"] != result["revision_id"]:
                directory = self._revisions(target)
                result["bpmn_xml"] = (
                    self._file(directory, f"{head['revision_id']}.bpmn").read_bytes().decode("utf-8")
                )
                if not isinstance(head.get("checks"), dict) or not isinstance(head.get("issues"), list):
                    raise PipelineError("STORAGE_CORRUPT", "Отчёт текущей ревизии повреждён.")
                result["checks"] = head["checks"]
                result["revision_issues"] = head["issues"]
                for key in ("semantic_diff", "semantic_review", "behavior"):
                    if key in head:
                        result[key] = head[key]
            result.update({"revision_id": head["revision_id"], "audit_stale": head["audit_stale"]})
            return result

    def revise(self, generation_id: str, base: str, xml: str, validation: dict) -> dict:
        if not _valid_id(base):
            raise PipelineError("REVISION_CONFLICT", "Некорректная базовая ревизия.")
        with self._exclusive():
            target = self.directory(generation_id)
            head = self._head(target)
            if head["revision_id"] != base:
                raise PipelineError(
                    "REVISION_CONFLICT", "Схема уже имеет новую ревизию. Обновите её перед сохранением."
                )
            if self._read_json(target, "result.json").get("bpmn_xml") is None:
                raise PipelineError("REVISION_UNAVAILABLE", "У генерации нет схемы для редактирования.")
            revision_id = uuid.uuid4().hex
            directory = self._revisions(target, create=True)
            self._text(directory / f"{revision_id}.bpmn", xml)
            head = {
                "revision_id": revision_id,
                "base_revision_id": base,
                "audit_stale": validation.get("semantic_diff", {}).get("status") != "unchanged",
                "checks": validation["checks"],
                "issues": validation["issues"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            for key in ("semantic_diff", "semantic_review", "behavior"):
                if key in validation:
                    head[key] = validation[key]
            # Every revision keeps its own validation report; head publication is last.
            self._json(directory / f"{revision_id}.json", head)
            self._json(target / "head.json", head)
            return head

    def artifact(self, generation_id: str, kind: str) -> Path:
        if kind not in KINDS:
            raise PipelineError("NOT_FOUND", "Вид артефакта не найден.")
        return self._file(self.directory(generation_id), KINDS[kind])
