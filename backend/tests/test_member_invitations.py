import base64
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth, feishu_auth
from app.config import Settings
from app.core import account_access, feishu_permissions, feishu_tokens
from app.core.storage import SQLiteStore


@pytest.fixture
def invitation_client(tmp_path, monkeypatch):
    database = SQLiteStore(tmp_path / "invitations.sqlite3")
    settings = Settings(
        FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="test-secret",
        FEISHU_TENANT_KEY="tenant-test", FEISHU_REDIRECT_URI="http://127.0.0.1:8000/login",
        FEISHU_TOKEN_ENCRYPTION_KEY=base64.urlsafe_b64encode(b"k" * 32).decode(),
    )
    for module in (auth, feishu_auth, feishu_tokens, account_access, feishu_permissions):
        monkeypatch.setattr(module, "store", database)
    for module in (auth, feishu_auth, feishu_tokens):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    for account, role in (("admin", "admin"), ("employee", "employee")):
        database.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                         (account, account, "", database.now(), database.now()))
        database.execute("INSERT INTO account_memberships(account, role, updated_at) VALUES (?, ?, ?)",
                         (account, role, database.now()))
    admin = {"X-Auth-Token": auth.create_auth_session("admin", "admin", "admin")["data"]["token"]}
    employee = {"X-Auth-Token": auth.create_auth_session("employee", "employee", "employee")["data"]["token"]}
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(feishu_auth.router)
    with TestClient(app) as client:
        yield client, database, settings, admin, employee


def fake_feishu(monkeypatch):
    monkeypatch.setattr(feishu_tokens, "request_token", lambda fields: {
        "access_token": fields["code"], "refresh_token": "refresh-test", "expires_in": 3600,
        "refresh_token_expires_in": 86400, "scope": "offline_access",
    })
    monkeypatch.setattr(feishu_tokens, "verified_user", lambda access: {
        "tenant_key": "tenant-test", "open_id": f"ou_{access}", "name": "New employee",
    })


def create_link(client, headers):
    response = client.post("/admin/invitations", headers=headers)
    assert response.status_code == 201
    data = response.json()["data"]
    token = parse_qs(urlparse(data["url"]).fragment)["invite"][0]
    return response, data, token


def start_invited_login(client, token):
    response = client.post("/auth/feishu/start", json={"invitation": token})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def finish_invited_login(client, started, code="new-user"):
    return client.post("/auth/feishu/complete", json={
        "state": started["state"], "browser_secret": started["browser_secret"], "code": code,
    })


def test_admin_invitation_is_single_use_and_stores_only_a_hash(invitation_client, monkeypatch):
    client, database, _, admin, employee = invitation_client
    fake_feishu(monkeypatch)
    assert client.post("/admin/invitations").status_code == 401
    assert client.post("/admin/invitations", headers=employee).status_code == 403
    response, invitation, token = create_link(client, admin)
    assert response.headers["cache-control"] == "no-store"
    assert not urlparse(invitation["url"]).query
    row = database.query_one("SELECT * FROM account_invitations WHERE id = ?", (invitation["id"],))
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(dict(row))
    assert invitation["expires_at"] - row["created_at"] == 7 * 86400
    assert token not in client.get("/admin/invitations", headers=admin).text
    assert client.post("/auth/feishu/start", headers=employee, json={"invitation": token}).status_code == 400
    assert client.post("/auth/feishu/start", json={"invitation": "x" * 43}).status_code == 410

    started = start_invited_login(client, token)
    flow = database.query_one("SELECT * FROM feishu_oauth_flows")
    assert flow["invitation_id"] == invitation["id"] and token not in str(dict(flow))
    completed = finish_invited_login(client, started)
    assert completed.status_code == 200, completed.text
    account = completed.json()["data"]["account"]
    assert account["role"] == "employee"
    members = client.get("/admin/members", headers=admin).json()["data"]
    member = next(member for member in members if member["account"] == account["account"])
    assert member["has_feishu_grant"] and member["feishu_connected"]
    row = database.query_one("SELECT * FROM account_invitations WHERE id = ?", (invitation["id"],))
    assert row["redeemed_by"] == account["account"] and row["redeemed_at"] is not None
    assert client.post("/auth/feishu/start", json={"invitation": token}).status_code == 410
    assert client.get("/admin/invitations", headers=admin).json()["data"][0]["status"] == "redeemed"
    actions = {row["action"] for row in database.query_all("SELECT action FROM account_audit")}
    assert {"member_invitation_created", "member_invitation_redeemed"} <= actions


def test_invitation_does_not_change_an_existing_members_role(invitation_client, monkeypatch):
    client, database, _, admin, _ = invitation_client
    fake_feishu(monkeypatch)
    database.execute("INSERT INTO feishu_identities(account, app_id, tenant_key, open_id) VALUES (?, ?, ?, ?)",
                     ("admin", "cli_test", "tenant-test", "ou_existing"))
    _, _, token = create_link(client, admin)
    completed = finish_invited_login(client, start_invited_login(client, token), "existing")
    assert completed.status_code == 200
    assert completed.json()["data"]["account"]["role"] == "admin"
    assert database.query_one("SELECT role FROM account_memberships WHERE account = 'admin'")["role"] == "admin"


def test_revocation_and_expiry_prevent_ongoing_invited_login(invitation_client, monkeypatch):
    client, database, _, admin, _ = invitation_client
    fake_feishu(monkeypatch)
    _, invitation, token = create_link(client, admin)
    started = start_invited_login(client, token)
    assert client.delete(f"/admin/invitations/{invitation['id']}", headers=admin).status_code == 200
    assert finish_invited_login(client, started).status_code == 400
    assert client.post("/auth/feishu/start", json={"invitation": token}).status_code == 410

    _, expired, token = create_link(client, admin)
    started = start_invited_login(client, token)
    database.execute("UPDATE account_invitations SET expires_at = ? WHERE id = ?",
                     (database.now() - 1, expired["id"]))
    assert finish_invited_login(client, started).status_code == 410
    assert len(database.query_all("SELECT account FROM accounts")) == 2
    assert not database.query_all("SELECT * FROM feishu_grants")


def test_wrong_tenant_or_failed_grant_does_not_consume_invitation(invitation_client, monkeypatch):
    client, database, _, admin, _ = invitation_client
    fake_feishu(monkeypatch)
    _, invitation, token = create_link(client, admin)
    started = start_invited_login(client, token)
    def wrong_tenant(_access):
        raise feishu_tokens.FeishuAuthError("当前飞书账号不属于本企业。")
    monkeypatch.setattr(feishu_tokens, "verified_user", wrong_tenant)
    assert finish_invited_login(client, started).status_code == 400
    row = database.query_one("SELECT * FROM account_invitations WHERE id = ?", (invitation["id"],))
    assert row["redeemed_at"] is None

    fake_feishu(monkeypatch)
    started = start_invited_login(client, token)
    def fail_encryption(*_args):
        raise feishu_tokens.FeishuAuthError("加密失败")
    monkeypatch.setattr(feishu_tokens, "encrypt_token", fail_encryption)
    assert finish_invited_login(client, started).status_code == 400
    assert len(database.query_all("SELECT account FROM accounts")) == 2
    assert not database.query_all("SELECT * FROM feishu_identities")
    assert database.query_one("SELECT redeemed_at FROM account_invitations WHERE id = ?",
                              (invitation["id"],))["redeemed_at"] is None


def test_two_oauth_flows_cannot_redeem_one_invitation_twice(invitation_client, monkeypatch):
    client, database, _, admin, _ = invitation_client
    fake_feishu(monkeypatch)
    _, invitation, token = create_link(client, admin)
    started = [start_invited_login(client, token), start_invited_login(client, token)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda pair: finish_invited_login(client, pair[1], f"user-{pair[0]}"),
                                  enumerate(started)))
    assert sorted(response.status_code for response in responses) == [200, 410]
    assert len(database.query_all("SELECT * FROM feishu_identities")) == 1
    assert database.query_one("SELECT redeemed_at FROM account_invitations WHERE id = ?",
                              (invitation["id"],))["redeemed_at"] is not None


def test_missing_enterprise_config_prevents_invite_creation(invitation_client):
    client, database, settings, admin, employee = invitation_client
    settings.FEISHU_APP_ID = ""
    status = client.get("/auth/feishu/status", headers=admin).json()["data"]
    assert not status["enabled"] and "FEISHU_APP_ID" in status["configuration_issues"][0]
    assert "test-secret" not in str(status)
    assert client.get("/auth/feishu/status", headers=employee).json()["data"]["configuration_issues"] == []
    assert client.post("/admin/invitations", headers=admin).status_code == 503
    assert not database.query_all("SELECT * FROM account_invitations")
    settings.FEISHU_APP_ID = "cli_test"
    settings.FEISHU_TOKEN_ENCRYPTION_KEY = "invalid"
    status = client.get("/auth/feishu/status", headers=admin).json()["data"]
    assert status["enabled"] and "加密密钥配置无效" in status["configuration_issues"][0]
    settings.FEISHU_TOKEN_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()
    settings.FEISHU_REDIRECT_URI = "https://[invalid/login"
    status = client.get("/auth/feishu/status", headers=admin).json()["data"]
    assert "回调地址配置无效" in status["configuration_issues"][0]


def test_stored_grant_can_be_stopped_and_retried_when_configuration_is_incomplete(invitation_client, monkeypatch):
    client, database, settings, admin, _ = invitation_client
    fake_feishu(monkeypatch)
    _, _, token = create_link(client, admin)
    completed = finish_invited_login(client, start_invited_login(client, token))
    session = completed.json()["data"]
    headers = {"X-Auth-Token": session["token"]}
    settings.FEISHU_APP_ID = ""
    status = client.get("/auth/feishu/status", headers=headers).json()["data"]
    assert not status["enabled"] and status["has_grant"] and not status["connected"]
    assert status["granted_scopes"] == []
    disconnected = client.delete("/auth/feishu/connection", headers=headers).json()["data"]
    assert disconnected["disconnected"] and not disconnected["remote_revoked"]
    status = client.get("/auth/feishu/status", headers=headers).json()["data"]
    assert status["has_grant"] and not status["connected"]
    member = next(item for item in client.get("/admin/members", headers=admin).json()["data"]
                  if item["account"] == session["account"]["account"])
    assert member["has_feishu_grant"] and not member["feishu_connected"]
    assert database.query_one("SELECT revoked FROM feishu_grants WHERE account = ?",
                              (session["account"]["account"],))["revoked"] == 1


def test_invitation_write_rechecks_admin_role_inside_transaction(invitation_client):
    client, database, _, admin, _ = invitation_client
    _, invitation, _ = create_link(client, admin)
    client.app.dependency_overrides[auth.get_admin_account] = lambda: auth.AccountInfo(
        account="employee", name="employee", role="admin",
    )
    assert client.post("/admin/invitations", headers=admin).status_code == 403
    assert client.delete(f"/admin/invitations/{invitation['id']}", headers=admin).status_code == 403
    assert database.query_one("SELECT revoked_at FROM account_invitations WHERE id = ?",
                              (invitation["id"],))["revoked_at"] is None


def test_invitation_route_is_registered_under_the_application_api_prefix(invitation_client):
    from app.main import create_app

    _, _, _, admin, _ = invitation_client
    client = TestClient(create_app())
    try:
        response = client.post("/api/v1/admin/invitations", headers=admin)
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
    finally:
        client.close()


def test_existing_oauth_flow_table_is_migrated_without_losing_rows(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE feishu_oauth_flows (
            state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, verifier TEXT NOT NULL,
            account TEXT, expires_at INTEGER NOT NULL
        )""")
        conn.execute("INSERT INTO feishu_oauth_flows VALUES ('state', 'browser', 'verifier', NULL, 123)")
    database = SQLiteStore(path)
    row = database.query_one("SELECT * FROM feishu_oauth_flows WHERE state_hash = 'state'")
    assert row["account_epoch"] == 0 and row["invitation_id"] is None
    SQLiteStore(path)
    assert database.query_one("SELECT COUNT(*) AS count FROM feishu_oauth_flows")["count"] == 1
