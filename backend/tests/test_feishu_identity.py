import base64
import asyncio
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth, feishu_auth
from app.config import Settings
from app.core import account_access, feishu_tokens, feishu_permissions
from app.core.storage import SQLiteStore


@pytest.fixture
def enterprise(tmp_path, monkeypatch):
    database = SQLiteStore(tmp_path / "enterprise.sqlite3")
    settings = Settings(
        FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="test-secret",
        FEISHU_TENANT_KEY="tenant-test", FEISHU_REDIRECT_URI="http://127.0.0.1:8000/login",
        FEISHU_TOKEN_ENCRYPTION_KEY=base64.urlsafe_b64encode(b"k" * 32).decode(),
    )
    for module in (auth, feishu_auth, feishu_tokens, account_access, feishu_permissions):
        monkeypatch.setattr(module, "store", database)
    for module in (feishu_tokens, feishu_auth, auth):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(feishu_auth.router)
    with TestClient(app) as client:
        yield client, database, settings


def grant(access="access-test", refresh="refresh-test"):
    return {"access_token": access, "refresh_token": refresh, "expires_in": 3600,
            "refresh_token_expires_in": 86400, "scope": "offline_access im:chat:read"}


def mock_identity(monkeypatch, open_id="ou_test"):
    monkeypatch.setattr(feishu_tokens, "request_token", lambda fields: grant())
    monkeypatch.setattr(feishu_tokens, "verified_user", lambda access: {
        "tenant_key": "tenant-test", "open_id": open_id, "name": "Employee",
    })


def complete(client):
    started = client.post("/auth/feishu/start").json()["data"]
    response = client.post("/auth/feishu/complete", json={
        "state": started["state"], "browser_secret": started["browser_secret"], "code": "test-code",
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_state_is_browser_bound_single_use_and_pkce_is_verified(enterprise, monkeypatch):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    captured = []
    monkeypatch.setattr(feishu_tokens, "request_token", lambda fields: captured.append(fields) or grant())
    started = client.post("/auth/feishu/start").json()["data"]
    query = parse_qs(urlparse(started["url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    body = {"state": started["state"], "browser_secret": "x" * 43, "code": "test-code"}
    assert client.post("/auth/feishu/complete", json=body).status_code == 400
    assert not captured
    body["browser_secret"] = started["browser_secret"]
    assert client.post("/auth/feishu/complete", json=body).status_code == 200
    challenge = base64.urlsafe_b64encode(hashlib.sha256(captured[0]["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [challenge]
    assert client.post("/auth/feishu/complete", json=body).status_code == 400
    assert len(captured) == 1
    row = database.query_one("SELECT * FROM feishu_grants")
    assert "access-test" not in row["encrypted_token"]
    with pytest.raises(feishu_tokens.FeishuAuthError):
        feishu_tokens.decrypt_token("another-account", row["encrypted_token"])


def test_devices_reuse_identity_but_different_people_are_isolated(enterprise, monkeypatch):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    phone = complete(client)
    desktop = complete(client)
    assert phone["account"] == desktop["account"]
    assert phone["token"] != desktop["token"]
    assert phone["account"]["role"] == "employee"
    mock_identity(monkeypatch, "ou_other")
    other = complete(client)
    assert other["account"]["account"] != phone["account"]["account"]
    assert len(database.query_all("SELECT * FROM feishu_identities")) == 2


def test_new_device_login_does_not_request_business_bundle(enterprise):
    client, _, _ = enterprise
    started = client.post("/auth/feishu/start").json()["data"]
    query = parse_qs(urlparse(started["url"]).query)
    assert query["scope"] == ["offline_access"]


def test_disabled_employee_cannot_return_via_oauth(enterprise, monkeypatch):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    user = complete(client)
    account = user["account"]["account"]
    database.execute("UPDATE account_memberships SET enabled = 0 WHERE account = ?", (account,))
    started = client.post("/auth/feishu/start").json()["data"]
    response = client.post("/auth/feishu/complete", json={**{k: started[k] for k in ("state", "browser_secret")}, "code": "code"})
    assert response.status_code == 403
    assert client.get("/auth/me", headers={"X-Auth-Token": user["token"]}).status_code == 401


def test_concurrent_refresh_rotates_only_once_and_survives_reopen(enterprise, monkeypatch):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    account = complete(client)["account"]["account"]
    database.execute("UPDATE feishu_grants SET expires_at = 1 WHERE account = ?", (account,))
    calls = []
    def refresh(fields):
        calls.append(fields)
        time.sleep(0.05)
        return grant("access-next", "refresh-next")
    monkeypatch.setattr(feishu_tokens, "request_token", refresh)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(feishu_tokens.access_token_for_account, [account] * 4))
    assert results == ["access-next"] * 4
    assert len(calls) == 1 and calls[0]["refresh_token"] == "refresh-test"
    reopened = SQLiteStore(database.db_path)
    row = reopened.query_one("SELECT * FROM feishu_grants WHERE account = ?", (account,))
    assert feishu_tokens.decrypt_token(account, row["encrypted_token"])["refresh_token"] == "refresh-next"


@pytest.mark.parametrize("revoked", [False, True])
def test_refresh_failure_preserves_or_invalidates_grant_correctly(enterprise, monkeypatch, revoked):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    account = complete(client)["account"]["account"]
    database.execute("UPDATE feishu_grants SET expires_at = 1 WHERE account = ?", (account,))
    before = database.query_one("SELECT encrypted_token FROM feishu_grants")[0]
    def fail(fields):
        raise feishu_tokens.FeishuAuthError("retry", reauthorize=revoked)
    monkeypatch.setattr(feishu_tokens, "request_token", fail)
    with pytest.raises(feishu_tokens.FeishuAuthError):
        feishu_tokens.access_token_for_account(account)
    row = database.query_one("SELECT * FROM feishu_grants")
    assert row["encrypted_token"] == before and bool(row["revoked"]) == revoked


def test_official_token_request_and_wrong_tenant_rejection(enterprise, monkeypatch):
    original_client = httpx.Client
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, **grant()})
        return httpx.Response(200, json={"code": 0, "data": {"open_id": "ou_x", "tenant_key": "wrong"}})
    monkeypatch.setattr(feishu_tokens.httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    assert feishu_tokens.request_token({"grant_type": "refresh_token", "refresh_token": "r"})["access_token"]
    assert str(requests[0].url) == "https://accounts.feishu.cn/oauth/v3/token"
    assert parse_qs(requests[0].content.decode())["client_secret"] == ["test-secret"]
    assert feishu_tokens.request_token({
        "grant_type": "authorization_code", "code": "test-code", "code_verifier": "v" * 43,
    })["access_token"]
    assert str(requests[1].url) == "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    assert requests[1].headers["content-type"] == "application/json"
    assert json.loads(requests[1].content)["code_verifier"] == "v" * 43
    with pytest.raises(feishu_tokens.FeishuAuthError, match="不属于本企业"):
        feishu_tokens.verified_user("access-test")


def test_device_session_expires(enterprise):
    client, database, settings = enterprise
    database.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                     ("local", "local", auth.hash_password("000000"), database.now(), database.now()))
    session = client.post("/auth/login", json={"account": "local", "password": "000000"}).json()["data"]
    database.execute("UPDATE auth_sessions SET created_at = ?", (database.now() - settings.AUTH_SESSION_DAYS * 86400,))
    assert client.get("/auth/me", headers={"X-Auth-Token": session["token"]}).status_code == 401


def test_incremental_authorization_reuses_grants_and_rejects_role_escalation(enterprise, monkeypatch):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    session = complete(client)
    headers = {"X-Auth-Token": session["token"]}
    ready = client.post("/auth/feishu/start", headers=headers, json={"scopes": ["im:chat:read"]})
    assert ready.json()["data"] == {"ready": True}
    start = client.post("/auth/feishu/start", headers=headers, json={"scopes": ["im:chat:read", "docx:document:create"]})
    assert parse_qs(urlparse(start.json()["data"]["url"]).query)["scope"] == ["docx:document:create offline_access"]
    assert client.post("/auth/feishu/start", headers=headers, json={"scopes": ["im:chat:create_by_user"]}).status_code == 403
    assert client.post("/auth/feishu/start", headers=headers, json={"scopes": ["application:application:write"]}).status_code == 403
    assert client.get("/auth/feishu/status", headers=headers).json()["data"]["connected"]


def test_policy_rechecks_current_role_for_every_command(enterprise, monkeypatch):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    account = complete(client)["account"]["account"]
    assert feishu_permissions.check_command_permission(account, ["lark-cli", "im", "+chat-list", "--as", "user"]) == []
    command = ["lark-cli", "im", "+chat-create", "--name", "test"]
    with pytest.raises(ValueError, match="当前角色"):
        feishu_permissions.check_command_permission(account, command)
    database.execute("UPDATE account_memberships SET role = 'lead' WHERE account = ?", (account,))
    assert feishu_permissions.check_command_permission(account, command) == ["im:chat:create_by_user"]
    database.execute("UPDATE account_memberships SET role = 'employee' WHERE account = ?", (account,))
    with pytest.raises(ValueError, match="当前角色"):
        feishu_permissions.check_command_permission(account, command)
    for unsafe in (
        ["lark-cli", "api", "/arbitrary"],
        ["lark-cli", "config", "init"],
        ["lark-cli", "im", "+chat-list", "--as=bot"],
        ["lark-cli", "im", "+chat-list", "--profile=admin"],
        ["lark-cli", "im", "+chat-list", "nested-command"],
    ):
        with pytest.raises(ValueError):
            feishu_permissions.check_command_permission(account, unsafe)


@pytest.mark.parametrize("role", ["", "owner", "administrator", "Employee"])
def test_unknown_roles_cannot_receive_capabilities_or_scopes(role):
    with pytest.raises(ValueError, match="未知网站角色"):
        feishu_permissions.capabilities_for_role(role)
    with pytest.raises(ValueError, match="未知网站角色"):
        feishu_permissions.scopes_for_role(role)


@pytest.mark.parametrize("params", [{}, {"check_security_conf": False},
                                   {"check_security_conf": "true"}, None])
def test_raw_member_query_cannot_skip_security_checks(enterprise, monkeypatch, params):
    client, _, _ = enterprise
    mock_identity(monkeypatch)
    account = complete(client)["account"]["account"]
    command = ["lark-cli", "im", "chat.members", "get"]
    if params is not None:
        command += ["--params", json.dumps({"chat_id": "oc_test", **params})]
    with pytest.raises(ValueError, match="安全检查"):
        feishu_permissions.check_command_permission(account, command)
    safe = ["lark-cli", "im", "chat.members", "get", "--params",
            json.dumps({"chat_id": "oc_test", "check_security_conf": True})]
    assert feishu_permissions.check_command_permission(account, safe) == ["im:chat.members:read"]
    with pytest.raises(ValueError, match="安全检查"):
        feishu_permissions.check_command_permission(account, safe + ["--params={}"])


def test_all_roles_share_read_capabilities_but_not_project_management():
    employee = set(feishu_permissions.capabilities_for_role("employee"))
    for key in ("documents_read", "groups_read", "group_members_read", "messages_read",
                "people_search", "calendar_read", "calendar_availability", "tasks_read",
                "meetings_read", "minutes_search", "minutes_read", "base_read"):
        assert key in employee
    assert "groups_create" not in employee
    assert "tasklists_write" not in employee
    assert employee < set(feishu_permissions.capabilities_for_role("lead"))
    assert "wiki:node:retrieve" in feishu_permissions.scopes_for_role("employee")


def test_conditional_authorization_does_not_block_plain_operations(enterprise, monkeypatch):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    account = complete(client)["account"]["account"]
    database.execute("UPDATE feishu_grants SET scopes=? WHERE account=?",
                     ("docx:document:readonly docx:document:write_only docs:document.media:upload", account))
    command = ["lark-cli", "docs", "+update"]
    assert feishu_permissions.check_command_permission(account, command) == []
    assert feishu_permissions.check_command_permission(account, command, authorization_only=True) == ["wiki:node:retrieve"]
    raw = ["lark-cli", "im", "chat.members", "get"]
    assert feishu_permissions.check_command_permission(account, raw, authorization_only=True) == ["im:chat.members:read"]
    with pytest.raises(ValueError, match="安全检查"):
        feishu_permissions.check_command_permission(account, raw)


def test_disconnect_blocks_use_even_when_remote_revoke_fails(enterprise, monkeypatch):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    session = complete(client)
    headers = {"X-Auth-Token": session["token"]}
    original_client = httpx.Client
    monkeypatch.setattr(feishu_tokens.httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503)), **kwargs,
    ))
    response = client.delete("/auth/feishu/connection", headers=headers)
    assert response.json()["data"]["disconnected"]
    assert not response.json()["data"]["remote_revoked"]
    assert database.query_one("SELECT revoked FROM feishu_grants")[0] == 1
    assert database.query_one("SELECT * FROM feishu_identities") is not None
    with pytest.raises(feishu_tokens.FeishuAuthError):
        feishu_tokens.access_token_for_account(session["account"]["account"])
    assert client.get("/auth/me", headers=headers).status_code == 200


def test_disconnected_inflight_callback_cannot_restore_grant(enterprise, monkeypatch):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    session = complete(client)
    account = session["account"]["account"]
    started = client.post("/auth/feishu/start", headers={"X-Auth-Token": session["token"]},
                          json={"scopes": ["docx:document:create"]}).json()["data"]
    def revoke_during_identity_check(access):
        database.execute("INSERT INTO account_auth_epochs VALUES (?, 1)", (account,))
        return {"tenant_key": "tenant-test", "open_id": "ou_test", "name": "Employee"}
    monkeypatch.setattr(feishu_tokens, "verified_user", revoke_during_identity_check)
    response = client.post("/auth/feishu/complete", json={
        "state": started["state"], "browser_secret": started["browser_secret"], "code": "code",
    })
    assert response.status_code == 409


@pytest.mark.parametrize("command, scopes", [
    (["docs", "+fetch", "--doc", "test"], "docx:document:readonly"),
    (["base", "+table-list", "--base-token", "test"], "base:table:read"),
    (["base", "+field-list", "--base-token", "test"], "base:field:read"),
    (["base", "+record-list", "--base-token", "test"], "base:record:read"),
    (["base", "+record-search", "--base-token", "test"], "base:record:read"),
    (["base", "+record-batch-create", "--base-token", "test"], "base:record:create"),
    (["base", "+record-batch-update", "--base-token", "test"], "base:record:update"),
    (["docs", "+create", "--title", "test"], "docx:document:create"),
    (["docs", "+update", "--doc", "test"], "docx:document:readonly docx:document:write_only"),
    (["calendar", "events", "create"], "calendar:calendar.event:create"),
    (["calendar", "event.attendees", "create"], "calendar:calendar.event:update"),
    (["calendar", "+freebusy", "--type", "common_free"], "calendar:calendar.free_busy:read"),
    (["calendar", "+suggestion"], "calendar:calendar.free_busy:read"),
    (["minutes", "+search", "--query", "test"], "minutes:minutes.search:read"),
    (["minutes", "+detail", "--minute-tokens", "test"], "minutes:minutes.basic:read"),
    (["minutes", "+detail", "--minute-tokens", "test", "--summary=false"], "minutes:minutes.basic:read"),
    (["minutes", "+detail", "--minute-tokens", "test", "--summary"], "minutes:minutes.basic:read minutes:minutes.artifacts:read"),
    (["calendar", "+room-find", "--slot", "2026-09-09T14:00:00+08:00~2026-09-09T15:00:00+08:00"], "calendar:calendar.free_busy:read"),
    (["im", "+messages-search", "--query", "test"], "search:message im:message.reactions:read"),
])
def test_operation_does_not_require_unrelated_bundle_scopes(enterprise, monkeypatch, command, scopes):
    client, database, _ = enterprise
    mock_identity(monkeypatch)
    account = complete(client)["account"]["account"]
    database.execute("UPDATE feishu_grants SET scopes = ? WHERE account = ?", (scopes, account))
    assert feishu_permissions.check_command_permission(account, ["lark-cli", *command]) == []


@pytest.mark.parametrize("executable", [["/usr/local/bin/lark-cli"], ["/usr/bin/node", "/opt/lark/cli.js"]])
def test_enterprise_execution_checks_command_before_launcher_resolution(enterprise, monkeypatch, executable):
    from app.skills.lark_cli import skill_runtime

    client, _, settings = enterprise
    mock_identity(monkeypatch)
    account = complete(client)["account"]["account"]
    monkeypatch.setattr(skill_runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(skill_runtime.LarkCLISkill, "_split_lark_cli_args",
                        staticmethod(lambda command: [*executable, "im", "+chat-list", "--as", "user"]))
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (b'{"ok":true}', b'')
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    ok, _, error = asyncio.run(skill_runtime.LarkCLISkill().execute_command(
        "lark-cli im +chat-list --as user", user_id=account,
    ))
    assert ok, error
    assert list(spawn.call_args.args[:len(executable)]) == executable
    assert spawn.call_args.kwargs["env"]["LARKSUITE_CLI_USER_ACCESS_TOKEN"] == "access-test"
