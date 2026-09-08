import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth, models, scheduled_tasks
from app.core import account_access
from app.core.storage import SQLiteStore
from app.skills.lark_cli.skill_runtime import LarkCLISkill


@pytest.fixture
def account_client(tmp_path, monkeypatch):
    database = SQLiteStore(tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(auth, "store", database)
    monkeypatch.setattr(account_access, "store", database)
    # Explicit test fixtures, not insecure application bootstrap accounts.
    for account, role in (("admin", "admin"), ("local", "employee")):
        database.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                         (account, account, auth.hash_password("000000"), database.now(), database.now()))
        database.execute("INSERT INTO account_memberships(account, role, updated_at) VALUES (?, ?, ?)",
                         (account, role, database.now()))
    app = FastAPI()
    for router in (auth.router, models.router, scheduled_tasks.router):
        app.include_router(router)
    with TestClient(app) as client:
        yield client, database


def login(client, account):
    response = client.post("/auth/login", json={"account": account, "password": "000000"})
    assert response.status_code == 200
    return {"X-Auth-Token": response.json()["data"]["token"]}


def test_two_devices_and_logout_are_independent(account_client):
    client, _ = account_client
    phone = login(client, "local")
    desktop = login(client, "local")
    assert phone != desktop
    assert client.get("/auth/me", headers=phone).json()["data"]["role"] == "employee"
    client.post("/auth/logout", headers=phone)
    assert client.get("/auth/me", headers=phone).status_code == 401
    assert client.get("/auth/me", headers=desktop).status_code == 200


def test_configuration_requires_admin_without_mutation(account_client, monkeypatch):
    client, _ = account_client
    employee = login(client, "local")
    monkeypatch.setattr(models, "apply_model_config", lambda _: pytest.fail("must not mutate config"))
    assert client.post("/models/config", headers=employee, json={"preset": "deepseek"}).status_code == 403
    assert client.post("/scheduled-tasks/config", headers=employee, json={"enabled": False}).status_code == 403
    assert client.get("/admin/members", headers=employee).status_code == 403


def test_role_changes_apply_to_existing_sessions_and_last_admin_is_protected(account_client):
    client, database = account_client
    admin = login(client, "admin")
    local = login(client, "local")
    assert client.patch("/admin/members/admin", headers=admin, json={"enabled": False}).status_code == 409
    assert client.patch("/admin/members/local", headers=admin, json={"role": "admin"}).status_code == 200
    assert client.get("/admin/members", headers=local).status_code == 200
    assert client.patch("/admin/members/admin", headers=local, json={"role": "lead"}).status_code == 200
    assert client.get("/admin/members", headers=admin).status_code == 403
    assert client.get("/auth/me", headers=admin).json()["data"]["role"] == "lead"
    SQLiteStore(database.db_path)
    assert database.query_one("SELECT role FROM account_memberships WHERE account = 'admin'")["role"] == "lead"
    assert len(database.query_all("SELECT * FROM account_audit")) == 2


def test_disable_revokes_all_devices_and_reenable_requires_login(account_client):
    client, _ = account_client
    admin = login(client, "admin")
    devices = [login(client, "local"), login(client, "local")]
    assert client.patch("/admin/members/local", headers=admin, json={"enabled": False}).status_code == 200
    assert all(client.get("/auth/me", headers=device).status_code == 401 for device in devices)
    assert client.post("/auth/login", json={"account": "local", "password": "000000"}).status_code == 401
    assert client.patch("/admin/members/local", headers=admin, json={"enabled": True}).status_code == 200
    assert all(client.get("/auth/me", headers=device).status_code == 401 for device in devices)
    login(client, "local")


def test_disabled_account_cannot_execute_or_run_schedule(account_client, monkeypatch):
    from app.core import scheduled_tasks as scheduler

    client, _ = account_client
    admin = login(client, "admin")
    client.patch("/admin/members/local", headers=admin, json={"enabled": False})
    spawn = AsyncMock(side_effect=AssertionError("must not start a process"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = asyncio.run(LarkCLISkill().execute_command("lark-cli im +chat-list", user_id="local"))
    assert result[0] is False and "停用" in result[2]
    spawn.assert_not_called()
    failures = []
    monkeypatch.setattr(scheduler.scheduled_task_store, "fail_run", lambda task, payload: failures.append(payload))
    monkeypatch.setattr(scheduler, "LarkCLISkill", lambda: pytest.fail("must not start the agent"))
    asyncio.run(scheduler.ScheduledTaskRunner()._run_task({"id": 9, "user_id": "local"}))
    assert len(failures) == 1 and failures[0]["success"] is False


def test_unknown_account_cannot_execute(account_client):
    assert account_access.account_execution_error("missing")
