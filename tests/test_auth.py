"""Real password hashes, cookies, CSRF, expiry, and account-bound artifact access."""

import asyncio
import hashlib
import json
import sqlite3
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.auth import COOKIE_NAME, SESSION_SECONDS, AuthError, AuthRateLimit, AuthStore
from backend.app.main import create_app
from backend.app.storage import Storage
from tests.auth_helpers import AUTH_HEADERS, TEST_CREDENTIALS, authorize
from tests.test_api import TEXT, TrustedFixtureRunner, settings_for


@pytest.fixture
def app(tmp_path):
    return create_app(settings_for(tmp_path), runner=TrustedFixtureRunner())


def test_local_provision_hashes_secrets_and_login_returns_private_cookie(app):
    user_data = app.state.auth.create_user(**TEST_CREDENTIALS)
    assert user_data["username"] == TEST_CREDENTIALS["username"]
    with app.state.auth.connection() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    with TestClient(app) as client:
        assert client.get("/api/auth/session").json() == {"user": None, "csrf_token": None}
        response = client.post("/api/auth/login", json=TEST_CREDENTIALS, headers=AUTH_HEADERS)
        assert response.status_code == 200
        session = response.json()
        assert session["user"] == user_data
        assert session["user"]["username"] == TEST_CREDENTIALS["username"]
        assert len(session["user"]["id"]) == 32 and len(session["csrf_token"]) == 43
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
        assert f"Max-Age={SESSION_SECONDS}" in cookie and "Secure" not in cookie
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/auth/session").json() == session
        token = client.cookies.get(COOKIE_NAME)
        with app.state.auth.connection() as connection:
            user = connection.execute("SELECT * FROM users").fetchone()
            stored = connection.execute("SELECT * FROM sessions").fetchone()
        assert len(user["password_salt"]) == 32 and len(user["password_hash"]) == 32
        assert stored["token_hash"] == hashlib.sha256(token.encode()).digest()
        database = app.state.auth.path.read_bytes()
        assert TEST_CREDENTIALS["password"].encode() not in database
        assert token.encode() not in database
        assert TEST_CREDENTIALS["password"] not in response.text


def test_secure_cookie_configuration_works_with_https(tmp_path):
    app = create_app(settings_for(tmp_path, auth_cookie_secure=True), runner=TrustedFixtureRunner())
    app.state.auth.create_user(**TEST_CREDENTIALS)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/auth/login", json=TEST_CREDENTIALS, headers=AUTH_HEADERS)
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/auth/session").json()["user"] is not None


def test_login_case_insensitive_logout_revokes_and_accounts_survive_restart(app, tmp_path):
    with TestClient(app) as client:
        initial = authorize(client)
        old_token = client.cookies.get(COOKIE_NAME)
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/session").json()["user"] is None
        assert app.state.auth.session(old_token) is None
    restarted = create_app(settings_for(tmp_path), runner=TrustedFixtureRunner())
    with TestClient(restarted) as client:
        response = client.post(
            "/api/auth/login", headers=AUTH_HEADERS,
            json={**TEST_CREDENTIALS, "username": TEST_CREDENTIALS["username"].upper()},
        )
        assert response.status_code == 200 and response.json()["user"] == initial["user"]
        assert client.cookies.get(COOKIE_NAME) != old_token
        assert response.json()["csrf_token"] != initial["csrf_token"]
        assert restarted.state.auth.session(client.cookies.get(COOKIE_NAME)) is not None


def test_wrong_password_and_unknown_account_share_error_and_no_session(app):
    with TestClient(app) as owner:
        authorize(owner)
    with TestClient(app) as client:
        credentials_list = (
            {**TEST_CREDENTIALS, "password": "incorrect-password-42"},
            {**TEST_CREDENTIALS, "username": "unknown-account"},
        )
        responses = [
            client.post("/api/auth/login", headers=AUTH_HEADERS, json=credentials)
            for credentials in credentials_list
        ]
        assert all(response.status_code == 401 for response in responses)
        assert responses[0].json() == responses[1].json()
        assert "set-cookie" not in responses[0].headers
        assert TEST_CREDENTIALS["password"] not in str([response.json() for response in responses])


def test_duplicate_username_and_salted_hashes(app):
    with TestClient(app) as client:
        authorize(client)
        with pytest.raises(AuthError) as raised:
            app.state.auth.create_user("API-TESTER", "another-synthetic-password")
        assert raised.value.status == 409
        assert client.post("/api/auth/login", json=TEST_CREDENTIALS, headers=AUTH_HEADERS).status_code == 200
        authorize(client, {**TEST_CREDENTIALS, "username": "second-user"})
    with app.state.auth.connection() as connection:
        users = connection.execute("SELECT password_hash, password_salt FROM users").fetchall()
    assert len(users) == 2
    assert users[0]["password_hash"] != users[1]["password_hash"]
    assert users[0]["password_salt"] != users[1]["password_salt"]


@pytest.mark.parametrize("credentials", [
    {}, {"username": "ab", "password": "synthetic-long-password"},
    {"username": "with space", "password": "synthetic-long-password"},
    {"username": "api-tester", "password": "short"},
    {"username": "api-tester", "password": "x" * 129},
    {"username": "api-tester", "password": "x" * 10 + "\ud800"},
    {**TEST_CREDENTIALS, "admin": True},
])
def test_bad_credentials_are_bounded_without_password_echo(app, credentials):
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login", content=json.dumps(credentials),
            headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert "password" not in response.text
        assert not client.cookies.get(COOKIE_NAME)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/generations"), ("get", "/api/generations/" + "a" * 32),
    ("get", "/api/generations/" + "a" * 32 + "/artifacts/source"),
    ("post", "/api/generate"), ("post", "/api/validate"),
    ("put", "/api/generations/" + "a" * 32 + "/revisions"),
    ("post", "/api/auth/logout"),
])
def test_all_private_routes_require_authentication_before_input_processing(app, method, path):
    with TestClient(app) as client:
        response = getattr(client, method)(path)
        assert response.status_code == 401
        assert response.json()["issues"][0]["code"] == "AUTH_REQUIRED"
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("headers", [
    {}, {"X-Requested-With": "other"},
    {**AUTH_HEADERS, "Origin": "http://evil.example"},
    {**AUTH_HEADERS, "Origin": "null"},
    {**AUTH_HEADERS, "Sec-Fetch-Site": "cross-site"},
])
def test_auth_rejects_missing_custom_header_and_cross_site_requests(app, headers):
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json=TEST_CREDENTIALS, headers=headers)
        assert response.status_code == 403
        assert not client.cookies.get(COOKIE_NAME)


def test_auth_rejects_form_submission_even_with_custom_header(app):
    with TestClient(app) as client:
        response = client.post("/api/auth/login", data=TEST_CREDENTIALS, headers=AUTH_HEADERS)
        assert response.status_code == 403


def test_csrf_required_bound_to_session_and_same_origin_is_accepted(app):
    with TestClient(app) as client, TestClient(app) as other:
        session = authorize(client)
        second_session = authorize(other, {**TEST_CREDENTIALS, "username": "other-user"})
        for token in ("", "wrong", second_session["csrf_token"]):
            response = client.post(
                "/api/validate", json={"xml": "<broken/>"}, headers={"X-CSRF-Token": token},
            )
            assert response.status_code == 403
        assert client.post(
            "/api/validate", json={"xml": "<broken/>"},
            headers={"X-CSRF-Token": session["csrf_token"], "Origin": "http://testserver"},
        ).status_code == 200
        assert client.post(
            "/api/validate", json={"xml": "<broken/>"}, headers={"Origin": "https://evil.example"},
        ).status_code == 403
        assert client.post("/api/auth/logout", headers={"X-CSRF-Token": ""}).status_code == 403
        assert client.get("/api/auth/session").json()["user"] is not None


def test_expired_session_is_rejected_and_cannot_logout_or_mutate(app):
    with TestClient(app) as client:
        authorize(client)
        with app.state.auth.connection() as connection:
            connection.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
        assert client.get("/api/auth/session").json() == {"user": None, "csrf_token": None}
        assert client.get("/api/generations").status_code == 401
        assert client.post("/api/generate", json={"description": TEXT}).status_code == 401
        assert client.post("/api/auth/logout").status_code == 401


def test_two_accounts_cannot_read_or_revise_each_others_generations(app):
    with TestClient(app) as alice, TestClient(app) as bob:
        alice_session = authorize(alice, {**TEST_CREDENTIALS, "username": "alice"})
        authorize(bob, {**TEST_CREDENTIALS, "username": "bobby"})
        result = alice.post("/api/generate", json={"description": TEXT}).json()
        assert result["status"] == "ready"
        generation_id = result["id"]
        assert alice.get(f"/api/generations/{generation_id}").status_code == 200
        assert bob.get(f"/api/generations/{generation_id}").status_code == 404
        for kind in ("source", "python", "ir", "xml", "checks", "metadata"):
            assert bob.get(f"/api/generations/{generation_id}/artifacts/{kind}").status_code == 404
        assert bob.put(f"/api/generations/{generation_id}/revisions", json={
            "xml": result["bpmn_xml"], "base_revision_id": result["revision_id"],
        }).status_code == 404
        assert bob.get("/api/generations").json() == {"items": []}
        history = alice.get("/api/generations").json()["items"]
        assert len(history) == 1 and history[0]["id"] == generation_id
        assert history[0]["status"] == "ready" and history[0]["created_at"]
        assert "python_code" not in history[0] and "bpmn_xml" not in history[0]
        expected = app.state.settings.data_dir / "users" / alice_session["user"]["id"] / generation_id
        assert expected.is_dir()


def test_generation_rate_limit_is_per_account_not_shared_proxy_address(app):
    with TestClient(app) as alice, TestClient(app) as bob:
        authorize(alice, {**TEST_CREDENTIALS, "username": "alice"})
        authorize(bob, {**TEST_CREDENTIALS, "username": "bobby"})
        for _ in range(12):
            assert alice.post("/api/generate", json={"description": "Unknown fixture"}).status_code == 200
        assert alice.post("/api/generate", json={"description": "Unknown fixture"}).status_code == 429
        assert bob.post("/api/generate", json={"description": "Unknown fixture"}).status_code == 200


def test_pre_auth_generation_artifacts_remain_unclaimed(app):
    # Legacy files are preserved, but never assigned to the first account.
    from tests.test_storage import generation

    result = generation()
    app.state.storage.save(result, "Legacy private process", {})
    with TestClient(app) as client:
        authorize(client)
        assert client.get("/api/generations").json()["items"] == []
        assert client.get(f"/api/generations/{result['id']}").status_code == 404
        assert app.state.storage.artifact(result["id"], "source").read_text() == "Legacy private process"


def test_rate_limit_bounds_attempts_memory_and_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("backend.app.auth.time.monotonic", lambda: now[0])
    limiter = AuthRateLimit()
    for _ in range(8):
        limiter.check("local", "same-user")
    with pytest.raises(AuthError, match="попыток") as raised:
        limiter.check("local", "SAME-USER")
    assert raised.value.status == 429
    now[0] += 901
    limiter.check("local", "same-user")
    for index in range(29):
        limiter.check("local", f"different-{index}")
    with pytest.raises(AuthError):
        limiter.check("local", "last-user")
    now[0] += 901
    for index in range(512):
        limiter.check(f"client-{index}", f"user-{index}")
    assert len(limiter.attempts) == 1024
    with pytest.raises(AuthError):
        limiter.check("new-client", "new-user")
    assert len(limiter.attempts) == 1024


def test_password_hash_concurrency_is_bounded(tmp_path):
    store = AuthStore(tmp_path)
    with store.hashing(), store.hashing(), pytest.raises(AuthError) as raised:
        store.create_user("example", "synthetic-password")
    assert raised.value.code == "AUTH_BUSY"


@pytest.mark.asyncio
async def test_password_hashing_does_not_block_health(app, monkeypatch):
    app.state.auth.create_user(**TEST_CREDENTIALS)
    with app.state.auth.connection() as connection:
        stored_hash = connection.execute("SELECT password_hash FROM users").fetchone()[0]
    entered, release = threading.Event(), threading.Event()

    def blocked_hash(_password, _salt):
        entered.set()
        assert release.wait(timeout=5)
        return stored_hash

    monkeypatch.setattr("backend.app.auth.password_hash", blocked_hash)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        login = asyncio.create_task(client.post(
            "/api/auth/login", json=TEST_CREDENTIALS, headers=AUTH_HEADERS,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            response = await asyncio.wait_for(client.get("/api/health"), timeout=1)
            assert response.status_code == 200 and not login.done()
        finally:
            release.set()
        assert (await login).status_code == 200


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("path", ["/api/auth/register", "/api/auth/register/"])
def test_registration_is_unavailable_and_cannot_create_accounts(app, authenticated, path):
    with TestClient(app) as client:
        if authenticated:
            authorize(client)
        with app.state.auth.connection() as connection:
            before = connection.execute("SELECT count(*) FROM users").fetchone()[0]
        for headers in (AUTH_HEADERS, {}):
            response = client.post(path, json={
                "username": "not-created", "password": "synthetic-disabled-registration",
            }, headers=headers)
            assert response.status_code == 404
            assert "set-cookie" not in response.headers
            assert response.headers["cache-control"] == "no-store"
        with app.state.auth.connection() as connection:
            assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == before
            assert connection.execute(
                "SELECT id FROM users WHERE username_key = 'not-created'"
            ).fetchone() is None
        assert "/api/auth/register" not in app.openapi()["paths"]


def test_user_storage_rejects_bad_identifier(tmp_path):
    storage = Storage(tmp_path)
    from backend.app.domain import PipelineError

    with pytest.raises(PipelineError):
        storage.for_user("../other")


def test_foreign_keys_prevent_orphan_session_rows(tmp_path):
    store = AuthStore(tmp_path)
    with store.connection() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)", (b"x", "missing", "y", 1, 2))
