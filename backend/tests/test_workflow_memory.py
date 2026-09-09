import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import chat, workflow_memories
from app.api.routes.auth import AccountInfo, get_current_account
from app.core import execution_records, workflow_memory
from app.core.execution_records import execution_record_store
from app.core.storage import SQLiteStore
from app.core.workflow_memory import workflow_memory_store
from app.skills.base import SkillContext
from app.skills.lark_cli.skill_runtime import LarkCLISkill, LarkCLIState


@pytest.fixture(autouse=True)
def legacy_engine_settings(monkeypatch):
    monkeypatch.setattr(LarkCLISkill, "settings", SimpleNamespace(LARK_AGENT_ENGINE="legacy"), raising=False)


def test_memory_detail_content_and_owner_isolation(memory_database):
    plan = {**_successful_plan(), "procedure": "先查群，再整理事项。"}
    memory = _record_success("owner", "整理项目群待办", plan)
    memory_database.execute(
        "UPDATE workflow_memories SET label = ? WHERE id = ?",
        ("自定义飞书工作流", memory["id"]),
    )
    app = FastAPI()
    app.include_router(workflow_memories.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="owner", name="Owner")
    with TestClient(app) as client:
        listed = client.get("/workflow-memories").json()["data"][0]
        assert listed["display_label"] == "整理项目群待办"
        detail = client.get(f"/workflow-memories/{memory['id']}").json()["data"]
        assert detail["source_request"] == "整理项目群待办"
        assert detail["procedure"] == "先查群，再整理事项。"
        assert detail["command_shapes"] == ["lark-cli im +chat-search"]
        assert "blueprint" not in detail
        app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="other", name="Other")
        assert client.get(f"/workflow-memories/{memory['id']}").status_code == 404
        app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="owner", name="Owner")
        memory_database.execute("DELETE FROM execution_records WHERE id = ?", (memory["last_execution_record_id"],))
        memory_database.execute("UPDATE workflow_memories SET blueprint_json = ? WHERE id = ?",
                                ('{"command_shapes": ["lark-cli calendar +create"]}', memory["id"]))
        legacy = client.get(f"/workflow-memories/{memory['id']}").json()["data"]
        assert legacy["source_request"] is None
        assert legacy["procedure"] == ""
        assert legacy["display_label"] == "lark-cli calendar +create"
        workflow_memory_store.disable("owner", memory["id"])
        assert client.get(f"/workflow-memories/{memory['id']}").status_code == 404


@pytest.fixture
def memory_database(tmp_path, monkeypatch):
    database = SQLiteStore(tmp_path / "memory.sqlite3")
    monkeypatch.setattr(workflow_memory, "store", database)
    monkeypatch.setattr(execution_records, "store", database)
    return database


def _successful_plan() -> dict:
    return {
        "intent_type": "novel_group_digest",
        "summary": "检查项目群并整理未回应事项",
        "relevant_skills": ["lark-im", "lark-task"],
        "need_confirmation": False,
        "commands": [
            {
                "command": 'lark-cli im +chat-search --query "项目群" --format json',
                "expected": "search",
            }
        ],
    }


@pytest.mark.parametrize("failed_kind", ["read", "write", None])
def test_completed_graph_learns_only_successful_steps(memory_database, failed_kind):
    records = [
        {"command": "lark-cli contact +search-user --query test", "success": False, "expected": failed_kind},
        {"command": "lark-cli im +chat-members-list --chat-id oc_test", "success": True, "expected": "read"},
    ]
    record_id = execution_record_store.add(user_id="owner", session_id="session", request="查询成员",
                                            executed_commands=records, success=True)
    memory = workflow_memory_store.record_success(
        user_id="owner", execution_record_id=record_id, request="查询成员",
        plan={"planning_source": "langgraph", "run_state": "completed", "intent_type": "agent_workflow", "commands": records},
        executed_commands=records,
    )
    if failed_kind != "read":
        assert memory is None
    else:
        assert memory is not None
        assert memory["status"] == "candidate"
        assert memory["blueprint"]["command_shapes"] == ["lark-cli im +chat-members-list"]


@pytest.mark.parametrize("state", [None, "waiting_approval", "waiting_authorization", "failed", "service_error"])
def test_graph_partial_reads_never_become_success_memory(memory_database, state):
    plan = {**_successful_plan(), "planning_source": "langgraph", "run_state": state}
    assert workflow_memory_store.record_success(user_id="owner", execution_record_id=1,
        request="查群后发送通知", plan=plan,
        executed_commands=[{"command": "lark-cli im +chat-search --query test", "success": True}]) is None


def _record_success(user_id: str, request: str, plan: dict, record_suffix: str = "") -> dict:
    record_id = execution_record_store.add(
        user_id=user_id,
        session_id=f"session{record_suffix}",
        request=request,
        plan=plan,
        executed_commands=[
            {
                "command": 'lark-cli im +chat-search --query "项目群" --format json',
                "success": True,
            }
        ],
        success=True,
    )
    memory = workflow_memory_store.record_success(
        user_id=user_id,
        execution_record_id=record_id,
        request=request,
        plan=plan,
        executed_commands=[
            {
                "command": 'lark-cli im +chat-search --query "项目群" --format json',
                "success": True,
            }
        ],
    )
    assert memory is not None
    return memory


def test_memory_is_candidate_then_active_after_two_verified_successes(memory_database):
    request = "检查项目群未回应事项并整理"
    first = _record_success("owner", request, _successful_plan(), "-1")
    assert first["status"] == "candidate"
    assert first["success_count"] == 1
    assert workflow_memory_store.find_matches("owner", request) == []

    second = _record_success("owner", request, _successful_plan(), "-2")
    assert second["id"] == first["id"]
    assert second["status"] == "active"
    assert second["success_count"] == 2
    assert workflow_memory_store.find_matches("other", request) == []

    matches = workflow_memory_store.find_matches("owner", request)
    assert len(matches) == 1 and matches[0]["exact_match"] is True
    remembered_plan = workflow_memory_store.reusable_plan("owner", matches[0], request)
    assert remembered_plan == _successful_plan()
    assert "stdout" not in str(matches[0]["blueprint"])
    assert "项目群" not in str(matches[0]["blueprint"])


def test_concurrent_successes_atomically_promote_one_memory(memory_database):
    request = "检查项目群未回应事项并整理"
    plan = _successful_plan()
    commands = [
        {
            "command": 'lark-cli im +chat-search --query "项目群" --format json',
            "success": True,
        }
    ]
    record_ids = [
        execution_record_store.add(
            user_id="owner",
            session_id=f"parallel-{index}",
            request=request,
            plan=plan,
            executed_commands=commands,
            success=True,
        )
        for index in range(2)
    ]

    def remember(record_id: int):
        return workflow_memory_store.record_success(
            user_id="owner",
            execution_record_id=record_id,
            request=request,
            plan=plan,
            executed_commands=commands,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(remember, record_ids))

    assert all(item is not None for item in results)
    memories = workflow_memory_store.list_for_user("owner")
    assert len(memories) == 1
    assert memories[0]["status"] == "active"
    assert memories[0]["success_count"] == 2


def test_manual_activation_and_failure_demotion_are_account_scoped(memory_database):
    memory = _record_success("owner", "检查项目群并整理", _successful_plan())
    assert workflow_memory_store.activate("other", memory["id"]) is None
    active = workflow_memory_store.activate("owner", memory["id"])
    assert active and active["status"] == "active"

    assert workflow_memory_store.record_failure("other", memory["id"]) is None
    first_failure = workflow_memory_store.record_failure("owner", memory["id"])
    assert first_failure and first_failure["status"] == "active"
    second_failure = workflow_memory_store.record_failure("owner", memory["id"])
    assert second_failure and second_failure["status"] == "disabled"
    assert workflow_memory_store.list_for_user("owner") == []

    relearned = _record_success("owner", "检查项目群并整理", _successful_plan(), "-relearn")
    assert relearned["id"] == memory["id"]
    assert relearned["status"] == "candidate"
    assert relearned["success_count"] == 1


def test_concurrent_failures_atomically_disable_memory(memory_database):
    memory = _record_success("owner", "检查项目群并整理", _successful_plan())
    workflow_memory_store.activate("owner", memory["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: workflow_memory_store.record_failure("owner", memory["id"]),
            range(2),
        ))

    assert all(item is not None for item in results)
    disabled = workflow_memory_store.get_for_user("owner", memory["id"], include_disabled=True)
    assert disabled and disabled["status"] == "disabled"
    assert disabled["failure_count"] == 2


def test_clarification_does_not_demote_memory_but_failed_command_does(memory_database):
    memory = _record_success("owner", "检查项目群并整理", _successful_plan())
    active = workflow_memory_store.activate("owner", memory["id"])
    assert active
    plan = {**_successful_plan(), "workflow_memory": workflow_memory_store.public_view(active)}

    chat._record_execution_and_memory(
        user_id="owner",
        session_id="session",
        request="检查项目群并整理",
        plan=plan,
        executed_commands=[],
        success=False,
    )
    unchanged = workflow_memory_store.get_for_user("owner", memory["id"])
    assert unchanged and unchanged["failure_count"] == 0

    chat._record_execution_and_memory(
        user_id="owner",
        session_id="session",
        request="检查项目群并整理",
        plan=plan,
        executed_commands=[{"command": "lark-cli im +chat-search", "success": False}],
        success=False,
    )
    failed = workflow_memory_store.get_for_user("owner", memory["id"])
    assert failed and failed["failure_count"] == 1


def test_exact_active_memory_reuses_plan_without_model(memory_database, monkeypatch):
    query = "检查项目群未回应事项并整理"
    memory = _record_success("owner", query, _successful_plan())
    workflow_memory_store.activate("owner", memory["id"])

    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill._skills_metadata = {}
    monkeypatch.setattr(skill, "_select_relevant_skills", lambda query: [])
    monkeypatch.setattr(skill, "_select_references", lambda *args: [])
    monkeypatch.setattr(skill, "_build_heuristic_plan", lambda query: None)
    planner = AsyncMock(side_effect=AssertionError("exact active memory must avoid the model"))
    monkeypatch.setattr(skill, "_plan_with_llm", planner)
    context = SkillContext(user_id="owner", session_id="", message=query)
    state = LarkCLIState(installed=True, configured=True, authenticated=True)
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=state))

    plan, _, _ = asyncio.run(
        skill._plan_commands(context, query, state, normalize_intent=False, llm_timeout=1)
    )
    assert plan["planning_source"] == "memory"
    assert plan["workflow_memory"]["id"] == memory["id"]
    assert plan["commands"] == _successful_plan()["commands"]
    planner.assert_not_awaited()

    reused_step = skill._build_reused_memory_step(plan, [])
    assert reused_step and reused_step["command"] == _successful_plan()["commands"][0]["command"]
    assert reused_step["expected"] == "search"
    assert skill._build_reused_memory_step(
        plan,
        [{"command": reused_step["command"], "success": True}],
    ) is None

    preview = asyncio.run(skill.preview_plan(context, query))
    assert preview["planning_source"] == "memory"
    assert preview["workflow_memory"]["id"] == memory["id"]
    assert preview["commands"][0]["write"] is False


def test_similar_memory_only_assists_model_and_never_reuses_old_plan(memory_database, monkeypatch):
    memory = _record_success(
        "owner",
        "检查项目群未回应事项并整理",
        _successful_plan(),
    )
    workflow_memory_store.activate("owner", memory["id"])

    query = "查看销售群没回复的问题并汇总"
    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill._skills_metadata = {}
    monkeypatch.setattr(skill, "_select_relevant_skills", lambda query: [])
    monkeypatch.setattr(skill, "_select_references", lambda *args: [])
    monkeypatch.setattr(skill, "_build_heuristic_plan", lambda query: None)
    model_plan = {
        "intent_type": "novel_group_digest",
        "summary": "检查销售群并汇总未回复事项",
        "confidence": 0.9,
        "commands": [
            {
                "command": 'lark-cli im +chat-search --query "销售群" --format json',
                "expected": "search",
            }
        ],
    }
    planner = AsyncMock(return_value=model_plan)
    monkeypatch.setattr(skill, "_plan_with_llm", planner)
    context = SkillContext(user_id="owner", session_id="", message=query)
    state = LarkCLIState(installed=True, configured=True, authenticated=True)

    plan, _, _ = asyncio.run(
        skill._plan_commands(context, query, state, normalize_intent=False, llm_timeout=1)
    )

    assert plan["planning_source"] == "memory_assisted_model"
    assert plan["commands"] == model_plan["commands"]
    assert plan["commands"] != _successful_plan()["commands"]
    assert "workflow_memory" not in plan
    assert context.metadata["workflow_memory_hints"][0]["id"] == memory["id"]
    assert context.metadata["workflow_memory_hints"][0]["exact_match"] is False
    planner.assert_awaited_once()


def test_exact_memory_strips_writes_and_commands_with_dynamic_ids(memory_database):
    query = "检查项目群并在必要时通知"
    plan = {
        "intent_type": "novel_group_check",
        "summary": "检查项目群并通知",
        "need_confirmation": True,
        "commands": [
            {
                "command": 'lark-cli im +chat-search --query "项目群" --format json',
                "expected": "search",
            },
            {
                "command": "lark-cli im +chat-messages-list --chat-id oc_old --format json",
                "expected": "read",
            },
            {
                "command": 'lark-cli im +messages-send --chat-id oc_old --text "提醒"',
                "expected": "write",
            },
        ],
    }
    executed = [{**item, "success": True} for item in plan["commands"]]
    record_id = execution_record_store.add(
        user_id="owner",
        session_id="session",
        request=query,
        plan=plan,
        executed_commands=executed,
        success=True,
    )
    memory = workflow_memory_store.record_success(
        user_id="owner",
        execution_record_id=record_id,
        request=query,
        plan=plan,
        executed_commands=executed,
    )
    assert memory
    active = workflow_memory_store.activate("owner", memory["id"])
    assert active
    match = workflow_memory_store.find_matches("owner", query)[0]

    reusable = workflow_memory_store.reusable_plan("owner", match, query)

    assert reusable
    assert reusable["need_confirmation"] is True
    assert reusable["commands"] == [plan["commands"][0]]
    assert "oc_old" not in str(reusable)
    assert "+messages-send" not in str(reusable)


def test_memory_api_never_exposes_other_accounts(memory_database):
    memory = _record_success("owner", "检查项目群并整理", _successful_plan())
    app = FastAPI()
    app.include_router(workflow_memories.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="other", name="Other")

    with TestClient(app) as client:
        assert client.get("/workflow-memories").json()["data"] == []
        assert client.post(f"/workflow-memories/{memory['id']}/activate").status_code == 404
        assert client.delete(f"/workflow-memories/{memory['id']}").status_code == 404

        app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="owner", name="Owner")
        public = client.get("/workflow-memories").json()["data"]
        assert len(public) == 1
        assert "last_request_hash" not in public[0]
        assert "blueprint" not in public[0]
        assert client.post(f"/workflow-memories/{memory['id']}/activate").json()["data"]["status"] == "active"
        assert client.delete(f"/workflow-memories/{memory['id']}").status_code == 200
        assert client.get("/workflow-memories").json()["data"] == []
