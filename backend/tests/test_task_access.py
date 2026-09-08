import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import task_access
from app.core.storage import SQLiteStore

TASK = "e297ddff-06ca-4166-b917-4ce57cd3a7a0"
OTHER = "e297ddff-06ca-4166-b917-4ce57cd3a7a1"


def entity(creator="ou_owner", assignee="ou_worker", tasklist="team", guid=TASK):
    return {"guid": guid, "creator": {"type": "user", "id": creator},
            "members": [{"type": "user", "role": "assignee", "id": assignee}],
            "tasklists": [{"tasklist_guid": tasklist}]}


@pytest.mark.parametrize("action", ["+update", "+assign", "+complete", "delete"])
def test_creator_can_manage_own_task(action):
    assert task_access.task_decision(entity(), "ou_owner", "employee", action, set()) == "creator"


@pytest.mark.parametrize("action", ["+update", "+assign", "delete"])
def test_assignee_cannot_modify_or_delete(action):
    with pytest.raises(ValueError, match="任务权限不足"):
        task_access.task_decision(entity(), "ou_worker", "employee", action, set())


def test_assignee_can_complete_but_follower_cannot():
    task = entity()
    assert task_access.task_decision(task, "ou_worker", "employee", "+complete", set()) == "assignee_complete"
    task["members"][0]["role"] = "follower"
    with pytest.raises(ValueError):
        task_access.task_decision(task, "ou_worker", "employee", "+complete", set())


@pytest.mark.parametrize("role,lists", [("admin", set()), ("admin", {"another"}), ("lead", {"team"}), ("employee", {"team"})])
def test_no_implicit_admin_or_lead_bypass(role, lists):
    with pytest.raises(ValueError):
        task_access.task_decision(entity(), "ou_manager", role, "delete", lists)


def test_admin_can_manage_configured_team_not_private_task():
    assert task_access.task_decision(entity(), "ou_manager", "admin", "delete", {"team"}) == "managed_tasklist_admin"
    with pytest.raises(ValueError):
        task_access.task_decision(entity(tasklist="private"), "ou_manager", "admin", "delete", {"team"})


@pytest.fixture
def gate(tmp_path, monkeypatch):
    database = SQLiteStore(tmp_path / "task.sqlite3")
    database.execute("INSERT INTO accounts(account,name,password_hash,created_at,updated_at) VALUES ('owner','Owner','',0,0)")
    database.execute("INSERT INTO feishu_identities(account,app_id,tenant_key,open_id) VALUES ('owner','app','tenant','ou_owner')")
    monkeypatch.setattr(task_access, "store", database)
    settings = SimpleNamespace(FEISHU_APP_ID="app", FEISHU_TENANT_KEY="tenant", FEISHU_MANAGED_TASKLIST_IDS="team")
    monkeypatch.setattr(task_access, "get_settings", lambda: settings)
    monkeypatch.setattr(task_access, "member_role", lambda _: "employee")
    return database


def reply(task):
    return True, json.dumps({"ok": True, "data": {"task": task}}), ""


def test_fresh_detail_and_audit_for_every_batch_target(gate):
    reader = AsyncMock(side_effect=[reply(entity()), reply(entity(creator="ou_other", guid=OTHER))])
    with pytest.raises(ValueError, match="任务权限不足"):
        asyncio.run(task_access.enforce_task_access("owner", ["lark-cli", "task", "+update", "--task-id", TASK + "," + OTHER], reader))
    assert reader.await_count == 2
    assert '"user_id_type": "open_id"' in reader.call_args_list[0].args[0]
    assert [r["decision"] for r in gate.query_all("SELECT decision FROM task_permission_audit ORDER BY id")] == ["creator", "denied"]


@pytest.mark.parametrize("result", [(False, "", "network"), (True, "{}", ""), reply(entity(guid=OTHER)), (True, "not JSON", "")])
def test_failed_or_mismatched_read_fails_closed(gate, result):
    with pytest.raises(ValueError):
        asyncio.run(task_access.enforce_task_access("owner", ["lark-cli", "task", "+complete", "--task-id", TASK], AsyncMock(return_value=result)))
    assert gate.query_one("SELECT decision FROM task_permission_audit")["decision"] == "denied"


def test_role_demotion_during_read_is_respected(gate, monkeypatch):
    role = ["admin"]
    monkeypatch.setattr(task_access, "member_role", lambda _: role[0])
    async def read(_):
        role[0] = "employee"
        return reply(entity(creator="ou_someone_else"))
    with pytest.raises(ValueError):
        asyncio.run(task_access.enforce_task_access("owner", ["lark-cli", "task", "+update", "--task-id", TASK], read))


@pytest.mark.parametrize("suffix", [["--task-id", TASK, "--task-id", OTHER], ["--task-id", "@server-file"],
                                     ["--task-id", "https://evil.test/?guid=" + TASK], ["--task-id", TASK + "," + TASK]])
def test_ambiguous_targets_rejected(suffix):
    with pytest.raises(ValueError):
        task_access.target_ids(["lark-cli", "task", "+update", *suffix], ("task", "+update"))


def test_task_link_and_delete_target():
    assert task_access.target_ids(["--task-id", "https://applink.feishu.cn/client/todo/detail?guid=" + TASK], ("task", "+update")) == [TASK]
    assert task_access.target_ids(["--params", json.dumps({"task_guid": TASK})], ("task", "tasks", "delete")) == [TASK]


def test_runtime_denies_unconfirmed_delete_before_any_process(gate, monkeypatch):
    from app.core import account_access, feishu_permissions, feishu_tokens
    from app.skills.lark_cli.skill_runtime import LarkCLISkill
    from app.skills.lark_cli import skill_runtime
    monkeypatch.setattr(account_access, "account_execution_error", lambda _: "")
    monkeypatch.setattr(feishu_permissions, "check_command_permission", lambda *args, **kwargs: [])
    monkeypatch.setattr(feishu_tokens, "enterprise_configured", lambda: True)
    monkeypatch.setattr(feishu_tokens, "access_token_for_account", lambda _: "test-token")
    monkeypatch.setattr(skill_runtime, "get_settings", lambda: SimpleNamespace(FEISHU_APP_ID="app"))
    process = AsyncMock()
    monkeypatch.setattr(skill_runtime.asyncio, "create_subprocess_exec", process)
    skill = LarkCLISkill.__new__(LarkCLISkill)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda _: {})
    monkeypatch.setattr(skill, "_with_user_profile", lambda command, _: command)
    cmd = "lark-cli task tasks delete --params '" + json.dumps({"task_guid": TASK}) + "' --as user"
    success, _, error = asyncio.run(skill.execute_command(cmd, user_id="owner", structured=True))
    assert not success and "确认" in error
    process.assert_not_awaited()


@pytest.mark.parametrize("creator", ["ou_owner", "ou_other"])
def test_runtime_confirmed_delete_reads_owner_then_supplies_cli_confirmation(gate, monkeypatch, creator):
    from app.core import account_access, feishu_permissions, feishu_tokens
    from app.skills.lark_cli import skill_runtime
    monkeypatch.setattr(account_access, "account_execution_error", lambda _: "")
    monkeypatch.setattr(feishu_permissions, "check_command_permission", lambda *args, **kwargs: [])
    monkeypatch.setattr(feishu_tokens, "enterprise_configured", lambda: True)
    monkeypatch.setattr(feishu_tokens, "access_token_for_account", lambda _: "test-token")
    monkeypatch.setattr(skill_runtime, "get_settings", lambda: SimpleNamespace(FEISHU_APP_ID="app"))
    calls = []
    async def create(*args, **kwargs):
        calls.append(args)
        data = {"task": entity(creator=creator)} if args[3] == "get" else {}
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(json.dumps({"ok": True, "data": data}).encode(), b"")))
    monkeypatch.setattr(skill_runtime.asyncio, "create_subprocess_exec", create)
    skill = skill_runtime.LarkCLISkill.__new__(skill_runtime.LarkCLISkill)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda _: {})
    monkeypatch.setattr(skill, "_with_user_profile", lambda command, _: command)
    cmd = "lark-cli task tasks delete --params '" + json.dumps({"task_guid": TASK}) + "' --as user"
    success, _, error = asyncio.run(skill.execute_command(cmd, user_id="owner", structured=True, approved_write=True))
    if creator != "ou_owner":
        assert not success and "任务权限不足" in error
        assert [args[3] for args in calls] == ["get"]
        return
    assert success, error
    assert [args[3] for args in calls] == ["get", "delete"]
    assert "--yes" not in calls[0] and calls[1][-1] == "--yes"
    calls.clear()
    success, _, _ = asyncio.run(skill.execute_command(cmd + " --yes", user_id="owner", structured=True, approved_write=True))
    assert not success and not calls


def test_model_cannot_supply_delete_confirmation(monkeypatch):
    from app.skills.lark_cli import tool_catalog
    schema = {"inputSchema": {"type": "object", "properties": {
        "params": {"type": "object"}, "yes": {"type": "boolean"}}, "required": ["params"]}}
    monkeypatch.setattr(tool_catalog, "_inspect_cli", AsyncMock(side_effect=["help", json.dumps(schema)]))
    description = asyncio.run(tool_catalog.describe_operation("task tasks delete"))
    assert "yes" not in description["input_schema"]["properties"]
    with pytest.raises(ValueError):
        tool_catalog.build_command("task tasks delete", {"params": {"task_guid": TASK}, "yes": True}, description)
