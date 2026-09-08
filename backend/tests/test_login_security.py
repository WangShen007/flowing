import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.routes import auth
from app.config import Settings
from app.core.login_guard import ACCOUNT_LIMIT, WINDOW_SECONDS, check_login_rate
from app.core.passwords import hash_password, needs_upgrade, verify_password
from app.core.storage import SQLiteStore


@pytest.fixture
def secured_auth(tmp_path, monkeypatch):
    database = SQLiteStore(tmp_path / "security.sqlite3")
    settings = Settings()
    monkeypatch.setattr(auth, "store", database)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(auth.router)
    with TestClient(app) as client:
        yield client, database, settings


def test_password_hash_is_salted_and_legacy_migration_supported():
    first, second = hash_password("correct horse battery"), hash_password("correct horse battery")
    assert first != second
    assert verify_password("correct horse battery", first)
    assert not verify_password("wrong", first)
    assert not needs_upgrade(first)
    legacy = hashlib.sha256(b"legacy-password").hexdigest()
    assert verify_password("legacy-password", legacy) and needs_upgrade(legacy)
    for invalid in ("", "pbkdf2_sha256$9999999999$bad$bad", "pbkdf2_sha256$x$s$d"):
        assert not verify_password("anything", invalid)


def test_empty_database_has_no_default_password_accounts(secured_auth):
    client, database, _ = secured_auth
    assert client.post("/auth/login", json={"account": "admin", "password": "000000"}).status_code == 401
    assert not database.query_all("SELECT * FROM accounts")


def test_explicit_bootstrap_creates_only_admin_and_does_not_overwrite(secured_auth):
    client, database, settings = secured_auth
    settings.AUTH_BOOTSTRAP_PASSWORD = "unique-test-password"
    response = client.post("/auth/login", json={"account": "admin", "password": "unique-test-password"})
    assert response.status_code == 200
    assert response.json()["data"]["account"]["role"] == "admin"
    assert len(database.query_all("SELECT * FROM accounts")) == 1
    settings.AUTH_BOOTSTRAP_PASSWORD = "another-test-password"
    assert client.post("/auth/login", json={"account": "admin", "password": "unique-test-password"}).status_code == 200


def test_weak_bootstrap_is_rejected(secured_auth):
    client, database, settings = secured_auth
    settings.AUTH_BOOTSTRAP_PASSWORD = "000000"
    assert client.post("/auth/login", json={"account": "admin", "password": "000000"}).status_code == 503
    assert not database.query_all("SELECT * FROM accounts")


def test_successful_legacy_login_upgrades_hash_without_losing_role(secured_auth):
    client, database, _ = secured_auth
    legacy = hashlib.sha256(b"legacy-password").hexdigest()
    database.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", ("old", "Old", legacy, 1, 1))
    database.execute("INSERT INTO account_memberships(account, role, updated_at) VALUES ('old', 'lead', 1)")
    assert client.post("/auth/login", json={"account": "old", "password": "wrong"}).status_code == 401
    assert database.query_one("SELECT password_hash FROM accounts")["password_hash"] == legacy
    response = client.post("/auth/login", json={"account": "old", "password": "legacy-password"})
    assert response.status_code == 200 and response.json()["data"]["account"]["role"] == "lead"
    encoded = database.query_one("SELECT password_hash FROM accounts")["password_hash"]
    assert encoded != legacy and verify_password("legacy-password", encoded)


def test_login_throttle_expires_and_does_not_extend_on_blocked_attempt(secured_auth, monkeypatch):
    client, database, _ = secured_auth
    monkeypatch.setattr(database, "now", lambda: 10000)
    for _ in range(ACCOUNT_LIMIT):
        assert client.post("/auth/login", json={"account": "absent", "password": "wrong"}).status_code == 401
    response = client.post("/auth/login", json={"account": "absent", "password": "wrong"})
    assert response.status_code == 429 and response.headers["Retry-After"] == str(WINDOW_SECONDS)
    monkeypatch.setattr(database, "now", lambda: 10000 + WINDOW_SECONDS)
    assert client.post("/auth/login", json={"account": "absent", "password": "wrong"}).status_code == 401


def test_rate_limit_is_atomic_across_connections(secured_auth):
    _, database, _ = secured_auth
    def attempt(index):
        try:
            check_login_rate(database, "same", f"address-{index}")
            return True
        except HTTPException as error:
            assert error.status_code == 429
            return False
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(attempt, range(20))) == ACCOUNT_LIMIT


def test_management_script_uses_same_hash_and_revokes_sessions(secured_auth):
    from data.manage_users import upsert_users

    _, database, _ = secured_auth
    user = {"account": "managed", "name": "Managed", "password": "unique-test-password"}
    with database.connect() as conn:
        upsert_users(conn, [user], dry_run=False)
    database.execute("INSERT INTO auth_sessions VALUES ('old-token','managed','Managed','now',1)")
    with database.connect() as conn:
        upsert_users(conn, [{**user, "password": "new-unique-password"}], dry_run=False)
    encoded = database.query_one("SELECT password_hash FROM accounts WHERE account='managed'")["password_hash"]
    assert verify_password("new-unique-password", encoded)
    assert not verify_password("unique-test-password", encoded)
    assert not database.query_all("SELECT * FROM auth_sessions")


def test_management_script_rejects_weak_passwords(tmp_path):
    import json

    from data.manage_users import load_users

    source = tmp_path / "users.json"
    source.write_text(json.dumps({"users": [{"account": "test", "password": "000000"}]}))
    with pytest.raises(ValueError, match="12 to 1024"):
        load_users(source)
