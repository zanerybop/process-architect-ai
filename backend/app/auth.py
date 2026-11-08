"""Local accounts and revocable server-side sessions; no external identity provider."""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from pathlib import Path

COOKIE_NAME = "process_architect_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 160 * 1024 * 1024
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,40}\Z")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM, dklen=32,
    )


class AuthRateLimit:
    """Bound both attempts and memory; spoofed forwarding headers are never trusted."""

    def __init__(self):
        self.attempts: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def check(self, client: str, username: str):
        now = time.monotonic()
        keys = (("ip:" + client, 30), ("user:" + username.casefold(), 8))
        with self.lock:
            for key in list(self.attempts):
                values = self.attempts[key]
                while values and values[0] <= now - 900:
                    values.popleft()
                if not values:
                    del self.attempts[key]
            if len(self.attempts) + sum(key not in self.attempts for key, _ in keys) > 1024:
                raise AuthError(429, "AUTH_RATE_LIMIT", "Слишком много попыток входа. Повторите позднее.")
            for key, limit in keys:
                if len(self.attempts.get(key, ())) >= limit:
                    raise AuthError(429, "AUTH_RATE_LIMIT", "Слишком много попыток входа. Повторите позднее.")
            for key, _ in keys:
                self.attempts.setdefault(key, deque()).append(now)


class AuthStore:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = data_dir.resolve() / "accounts.sqlite3"
        if self.path.is_symlink():
            raise RuntimeError("Auth database cannot be a symbolic link.")
        self.rate_limit = AuthRateLimit()
        # Applied inside worker threads, so slow password hashes never block the event loop.
        self.hash_slots = threading.BoundedSemaphore(2)
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash BLOB PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
            """)
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def hashing(self):
        # Absorb short bursts without increasing scrypt memory/concurrency.
        # This bounded wait runs in a worker thread and preserves overload rejection.
        if not self.hash_slots.acquire(timeout=2):
            raise AuthError(429, "AUTH_BUSY", "Вход занят. Повторите через несколько секунд.")
        try:
            yield
        finally:
            self.hash_slots.release()

    def _new_session(self, connection, user):
        now = time.time()
        token, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        # Keep at most ten active sessions per account, including this one.
        connection.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash NOT IN "
            "(SELECT token_hash FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 9)",
            (user["id"], user["id"]),
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
            (hashlib.sha256(token.encode("ascii")).digest(), user["id"], csrf_token,
             now, now + SESSION_SECONDS),
        )
        return token, {"user": {"id": user["id"], "username": user["username"]}, "csrf_token": csrf_token}

    def create_user(self, username: str, password: str):
        """Provision locally; this operation has no HTTP route and creates no session."""
        if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
            raise AuthError(422, "INVALID_CREDENTIALS", "Логин: 3–40 латинских букв, цифр, ., _ или -.")
        if not isinstance(password, str) or not 10 <= len(password) <= 128:
            raise AuthError(422, "INVALID_CREDENTIALS", "Пароль должен содержать от 10 до 128 символов.")
        try:
            password.encode("utf-8")
        except UnicodeError as error:
            raise AuthError(422, "INVALID_CREDENTIALS", "Недопустимые символы в пароле.") from error
        with self.hashing():
            salt = secrets.token_bytes(32)
            digest = password_hash(password, salt)
        user = {"id": uuid.uuid4().hex, "username": username}
        try:
            with self.connection() as connection:
                connection.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                    (user["id"], username, username.casefold(), salt, digest, time.time()),
                )
                return user
        except sqlite3.IntegrityError as error:
            raise AuthError(409, "USERNAME_TAKEN", "Этот логин уже занят.") from error

    def login(self, username: str, password: str):
        with self.connection() as connection:
            user = connection.execute(
                "SELECT * FROM users WHERE username_key = ?", (username.casefold(),)
            ).fetchone()
        with self.hashing():
            # Unknown accounts still pay the full password-hashing cost.
            salt = user["password_salt"] if user else b"process-architect-unknown-account"
            digest = password_hash(password, salt)
        if user is None or not hmac.compare_digest(digest, user["password_hash"]):
            raise AuthError(401, "INVALID_CREDENTIALS", "Неверный логин или пароль.")
        with self.connection() as connection:
            return self._new_session(connection, user)

    def session(self, token: str | None):
        if not token or not TOKEN_PATTERN.fullmatch(token):
            return None
        with self.connection() as connection:
            row = connection.execute(
                "SELECT users.id, users.username, sessions.csrf_token FROM sessions "
                "JOIN users ON users.id = sessions.user_id "
                "WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
                (hashlib.sha256(token.encode("ascii")).digest(), time.time()),
            ).fetchone()
        return (
            {"user": {"id": row["id"], "username": row["username"]}, "csrf_token": row["csrf_token"]}
            if row else None
        )

    def revoke(self, token: str | None):
        if token and TOKEN_PATTERN.fullmatch(token):
            with self.connection() as connection:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?",
                    (hashlib.sha256(token.encode("ascii")).digest(),),
                )
