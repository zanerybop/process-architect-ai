"""Local account provisioning and the password-stdin CLI never expose a registration API."""

import io
import os
import subprocess
import sys

import pytest

from backend.app.auth import AuthError, AuthStore
from backend.app.manage_users import main

PASSWORD = "synthetic-local-account-password"


@pytest.mark.parametrize("username,password", [
    ("ab", PASSWORD), ("x" * 41, PASSWORD), ("with space", PASSWORD), ("../user", PASSWORD),
    ("пользователь", PASSWORD), (None, PASSWORD), ("valid-user", "short"),
    ("valid-user", "x" * 129), ("valid-user", "x" * 10 + "\ud800"), ("valid-user", None),
])
def test_local_provision_validates_without_creating_user_or_session(tmp_path, username, password):
    store = AuthStore(tmp_path)
    with pytest.raises(AuthError) as raised:
        store.create_user(username, password)
    assert raised.value.status == 422
    if password:
        assert password not in str(raised.value)
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_cli_module_creates_account_using_stdin_and_never_creates_session(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "backend.app.manage_users", "create", "local-owner", "--password-stdin"],
        input=PASSWORD + "\n", text=True, capture_output=True, check=False, timeout=10,
        env={**os.environ, "DATA_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0
    assert result.stdout == "local-owner\n" and result.stderr == ""
    assert PASSWORD not in result.stdout + result.stderr
    store = AuthStore(tmp_path)
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    _, session = store.login("LOCAL-OWNER", PASSWORD)
    assert session["user"]["username"] == "local-owner"


def test_cli_duplicate_keeps_existing_password_and_sessions(tmp_path, monkeypatch, capsys):
    store = AuthStore(tmp_path)
    original = store.create_user("local-owner", PASSWORD)
    token, session = store.login("local-owner", PASSWORD)
    replacement = "synthetic-replacement-password"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(replacement + "\n"))
    assert main(["create", "LOCAL-OWNER", "--password-stdin"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "USERNAME_TAKEN" in captured.err
    assert PASSWORD not in captured.err and replacement not in captured.err
    assert store.session(token) == session
    assert store.login("local-owner", PASSWORD)[1]["user"] == original
    with pytest.raises(AuthError) as raised:
        store.login("local-owner", replacement)
    assert raised.value.code == "INVALID_CREDENTIALS"


@pytest.mark.parametrize("stdin", ["", "short\n", "x" * 129 + "\n", "x" * 10000 + "\n"])
def test_cli_rejects_invalid_stdin_without_echo(tmp_path, monkeypatch, capsys, stdin):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    assert main(["create", "local-owner", "--password-stdin"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "INVALID_CREDENTIALS" in captured.err
    if stdin:
        assert stdin.strip() not in captured.err
    with AuthStore(tmp_path).connection() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_cli_preserves_password_spaces_and_crlf_is_not_part_of_password(tmp_path, monkeypatch, capsys):
    password = " " + PASSWORD + " "
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(password + "\r\n"))
    assert main(["create", "local-owner", "--password-stdin"]) == 0
    assert capsys.readouterr().out == "local-owner\n"
    assert AuthStore(tmp_path).login("local-owner", password)[1]["user"]["username"] == "local-owner"


def test_cli_interactive_password_uses_getpass(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("backend.app.manage_users.getpass.getpass", lambda _prompt: PASSWORD)
    assert main(["create", "local-owner"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "local-owner\n" and captured.err == ""


def test_cli_storage_error_has_no_traceback_or_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))

    def unavailable(_self, _username, _password):
        raise OSError("synthetic-sensitive-database-path-" + PASSWORD)

    monkeypatch.setattr(AuthStore, "create_user", unavailable)
    assert main(["create", "local-owner", "--password-stdin"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "ACCOUNT_CREATE_FAILED" in captured.err
    assert PASSWORD not in captured.err and "Traceback" not in captured.err
