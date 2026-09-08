import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.routes import chat
from app.api.routes.auth import AccountInfo, get_current_account
from app.core import execution_records, local_sessions, workflow_checkpoints
from app.core.scheduled_tasks import (
    has_schedule_execution_intent,
    parse_schedule_intent,
    schedule_clarification_for_query,
)
from app.core.storage import SQLiteStore
from app.skills.base import SkillContext, SkillResult
from app.skills.lark_cli.skill_runtime import LarkCLISkill, LarkCLIState


@pytest.fixture(autouse=True)
def legacy_engine_settings(monkeypatch):
    # These tests exercise the retained legacy engine with __new__ test doubles.
    # The default LangGraph engine has independent end-to-end state tests.
    monkeypatch.setattr(LarkCLISkill, "settings", SimpleNamespace(LARK_AGENT_ENGINE="legacy"), raising=False)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "LARK_AGENT_ENGINE", "legacy")


@pytest.fixture
def database(tmp_path, monkeypatch):
    database = SQLiteStore(tmp_path / "workflow.sqlite3")
    for module in (local_sessions, execution_records, workflow_checkpoints):
        monkeypatch.setattr(module, "store", database)
    return database


def test_checkpoint_is_durable_owner_scoped_and_single_use(database):
    local_sessions.session_store.get_or_create("owner", "session")
    payload = {"query": "create then notify", "confirm_write": True, "executed_commands": [{"success": True}]}
    key = workflow_checkpoints.save_checkpoint("owner", "session", payload)
    assert SQLiteStore(database.db_path).query_one("SELECT status FROM workflow_checkpoints")["status"] == "waiting"
    assert workflow_checkpoints.claim_checkpoint(key, "other", "session") is None
    assert workflow_checkpoints.claim_checkpoint(key, "owner", "other") is None
    assert workflow_checkpoints.claim_checkpoint(key, "owner", "session") == payload
    assert workflow_checkpoints.claim_checkpoint(key, "owner", "session") is None


@pytest.mark.parametrize("state", ["waiting_approval", "failed", "service_error", "completed"])
def test_stream_success_uses_graph_terminal_state_not_successful_reads(database, monkeypatch, state):
    from app.core import workflow_memory
    monkeypatch.setattr(workflow_memory, "store", database)
    class FakeSkill:
        async def execute_stream(self, context, **kwargs):
            yield {"type": "metadata", "data": {
                "plan": {"planning_source": "langgraph", "intent_type": "agent_workflow", "run_state": state},
                "run_state": state, "approval_required": state == "waiting_approval",
                "executed_commands": [{"command": "lark-cli im +chat-search --query test", "success": True}],
            }}
            yield {"type": "content", "content": "等待确认" if state == "waiting_approval" else state}
    monkeypatch.setattr(chat, "LarkCLISkill", FakeSkill)
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="owner", name="Owner")
    with TestClient(app) as client:
        response = client.post("/chat", json={"message": "test", "session_id": "session", "stream": True})
        assert response.status_code == 200
    row = database.query_one("SELECT success FROM execution_records ORDER BY id DESC LIMIT 1")
    assert bool(row["success"]) is (state == "completed")


def test_authorization_resume_uses_server_checkpoint_not_client_query(database, monkeypatch):
    calls = []

    class FakeSkill:
        async def execute(self, context, **kwargs):
            calls.append((context, kwargs))
            if len(calls) == 1:
                return SkillResult(success=False, message="授权后继续", data={
                    "setup_required": True, "setup_scopes": ["test:read"],
                    "plan": {"summary": "create then notify"},
                    "executed_commands": [{"command": "created", "success": True}],
                })
            return SkillResult(success=True, message="已继续", data={})

    monkeypatch.setattr(chat, "LarkCLISkill", FakeSkill)
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="owner", name="Owner")
    with TestClient(app) as client:
        body = {"message": "create then notify", "session_id": "session", "stream": False, "confirm_write": True}
        first = client.post("/chat", json=body).json()["data"]
        key = first["metadata"]["resume_id"]
        resume = {**body, "message": "untrusted replacement", "resume_id": key, "confirm_write": False}
        assert client.post("/chat", json=resume).status_code == 200
        assert calls[-1][1]["query"] == body["message"]
        assert calls[-1][1]["confirm_write"] is True
        assert calls[-1][0].metadata["resume_checkpoint"]["executed_commands"][0]["success"]
        assert calls[-1][0].metadata["resume_checkpoint"]["plan"] == {"summary": "create then notify"}
        assert client.post("/chat", json=resume).status_code == 409


def test_cancel_stream_persists_interrupted_session(database, monkeypatch):
    entered = asyncio.Event()

    class SlowSkill:
        async def execute_stream(self, context, **kwargs):
            yield {"type": "progress", "content": "准备查询"}
            entered.set()
            await asyncio.sleep(30)
            raise AssertionError("must not continue after stop")

    monkeypatch.setattr(chat, "LarkCLISkill", SlowSkill)

    async def run():
        response = await chat.chat(chat.ChatRequest(message="测试查询", run_id="run", session_id="session"),
                                   AccountInfo(account="owner", name="Owner"))
        async def consume():
            async for _ in response.body_iterator:
                pass
        task = asyncio.create_task(consume())
        await entered.wait()
        assert len(local_sessions.session_store.get_session("owner", "session")["messages"]) == 1
        with pytest.raises(HTTPException) as error:
            await chat.chat(chat.ChatRequest(message="duplicate", session_id="session"),
                            AccountInfo(account="owner", name="Owner"))
        assert error.value.status_code == 409
        other = await chat.cancel_chat("run", AccountInfo(account="other", name="Other"))
        assert not other["data"]["cancelled"]
        await chat.cancel_chat("run", AccountInfo(account="owner", name="Owner"))
        await task

    asyncio.run(run())
    messages = local_sessions.session_store.get_session("owner", "session")["messages"]
    assert len(messages) == 2
    assert messages[-1]["metadata"]["run_state"] == "cancelled"
    assert messages[-1]["metadata"]["execution_trace"] == ["准备查询"]
    assert not chat._active_runs
    assert not chat._active_sessions


def test_cancel_command_terminates_local_process(monkeypatch):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    monkeypatch.setattr(skill, "_repair_command", lambda value: value)
    monkeypatch.setattr(skill, "_with_user_profile", lambda value, user: value)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda user: None)
    monkeypatch.setattr(
        skill,
        "_split_lark_cli_args",
        lambda value: [sys.executable, "-c", "import time; time.sleep(30)"],
    )

    async def run():
        original = asyncio.create_subprocess_exec
        processes = []
        started = asyncio.Event()
        async def create(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            started.set()
            return process
        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        task = asyncio.create_task(skill.execute_command("lark-cli test"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None

    asyncio.run(run())


def test_resume_runs_failed_step_without_repeating_successful_write(monkeypatch):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    state = LarkCLIState(installed=True, configured=True, authenticated=True)
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=state))
    planner = AsyncMock(side_effect=AssertionError("must reuse checkpoint plan"))
    monkeypatch.setattr(skill, "_plan_commands", planner)
    monkeypatch.setattr(skill, "_select_relevant_skills", lambda query: [])
    monkeypatch.setattr(skill, "_select_references", lambda query, selected: [])
    monkeypatch.setattr(skill, "_build_heuristic_step", lambda *args: {"done": True})
    monkeypatch.setattr(
        skill,
        "_maybe_enrich_slides_command",
        AsyncMock(side_effect=lambda value, command: (command, False)),
    )
    monkeypatch.setattr(skill, "_summarize_execution", AsyncMock(return_value="已完成"))
    execute = AsyncMock(return_value=(True, '{"ok":true}', ""))
    monkeypatch.setattr(skill, "execute_command", execute)
    context = SkillContext(user_id="owner", session_id="session", message="创建文档并通知", metadata={
        "resume_checkpoint": {"plan": {"summary": "继续", "commands": [
            {"command": "lark-cli im +messages-send --text test"},
        ]}, "executed_commands": [
            {"command": "lark-cli docs +create --title test", "success": True},
            {"command": "lark-cli im +messages-send --text test", "success": False, "expected": "write"},
        ]},
    })
    result = asyncio.run(skill.execute(context, confirm_write=True))
    assert result.success
    execute.assert_awaited_once()
    assert execute.call_args.args[0].startswith("lark-cli im")
    assert len(result.data["executed_commands"]) == 2
    assert len(result.data["plan"]["commands"]) == 1
    planner.assert_not_awaited()


def test_second_repair_success_resolves_all_failed_attempts_of_that_step(monkeypatch):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=LarkCLIState(
        installed=True, configured=True, authenticated=True,
    )))
    monkeypatch.setattr(skill, "_plan_commands", AsyncMock(return_value=({"commands": []}, [], [])))
    monkeypatch.setattr(skill, "_build_heuristic_step", lambda query, results: {"done": True} if
                        any(item.get("success") for item in results) else {
                            "command": "lark-cli im +chat-list", "expected": "read",
                        })
    monkeypatch.setattr(
        skill,
        "_maybe_enrich_slides_command",
        AsyncMock(side_effect=lambda value, command: (command, False)),
    )
    monkeypatch.setattr(skill, "execute_command", AsyncMock(side_effect=[
        (False, "", "invalid argument"), (False, "", "invalid argument"), (True, '{"ok":true}', ""),
    ]))
    monkeypatch.setattr(skill, "_repair_failed_command", AsyncMock(side_effect=[
        "lark-cli im +chat-list --page-size 10", "lark-cli im +chat-list --page-size 20",
    ]))
    monkeypatch.setattr(skill, "_summarize_execution", AsyncMock(return_value="已查询"))
    result = asyncio.run(skill.execute(SkillContext(user_id="owner", session_id="session", message="查询群"),
                                      query="查询群", confirm_write=True))
    assert result.success
    records = result.data["executed_commands"]
    assert len(records) == 3 and records[-1]["success"]
    assert all(item["superseded_by_repair"] == records[-1]["command"] for item in records[:-1])
    assert "部分操作未成功" not in skill._fallback_summary({}, records)


def test_direct_command_returns_readable_summary_and_retains_raw_evidence(monkeypatch):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=LarkCLIState(
        installed=True, configured=True, authenticated=True,
    )))
    raw = '{"ok":true,"data":{"items":[]}}'
    monkeypatch.setattr(skill, "execute_command", AsyncMock(return_value=(True, raw, "")))
    summarize = AsyncMock(return_value="没有找到符合条件的妙记。")
    monkeypatch.setattr(skill, "_summarize_execution", summarize)
    context = SkillContext(user_id="owner", session_id="session", message="查询妙记")
    result = asyncio.run(skill.execute(context, query="查询妙记", command="lark-cli minutes +search"))
    assert result.success and result.message == "没有找到符合条件的妙记。"
    assert result.data["stdout"] == raw
    assert result.data["executed_commands"][0]["stdout"] == raw
    assert summarize.call_args.args[0] == "查询妙记"


@pytest.mark.parametrize("mode", ["direct", "planned", "repair"])
def test_policy_denial_stops_workflow_without_repair_loop(monkeypatch, mode):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    state = LarkCLIState(installed=True, configured=True, authenticated=True)
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=state))
    monkeypatch.setattr(skill, "_plan_commands", AsyncMock(return_value=({"commands": []}, [], [])))
    monkeypatch.setattr(skill, "_build_heuristic_step", lambda *args: {
        "command": "lark-cli im +chat-list --as user", "expected": "read",
    })
    monkeypatch.setattr(
        skill,
        "_maybe_enrich_slides_command",
        AsyncMock(side_effect=lambda value, command: (command, False)),
    )
    denied = (False, "", "enterprise_policy_denied: 当前角色不能执行此操作")
    execute = AsyncMock(side_effect=[(False, "", "invalid argument"), denied] if mode == "repair" else [denied])
    repair = AsyncMock(return_value="lark-cli im +chat-create --name test")
    monkeypatch.setattr(skill, "execute_command", execute)
    monkeypatch.setattr(skill, "_repair_failed_command", repair)
    context = SkillContext(user_id="owner", session_id="session", message="查询群列表")
    result = asyncio.run(skill.execute(context, query="查询群列表", confirm_write=True,
                                      command="lark-cli config init" if mode == "direct" else None))
    assert not result.success and result.data["policy_denied"]
    assert result.message == "当前角色不能执行此操作"
    assert execute.await_count == (2 if mode == "repair" else 1)
    assert repair.await_count == (1 if mode == "repair" else 0)


def test_ambiguous_attendees_are_clarified_before_authorization(monkeypatch):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    probe = AsyncMock(side_effect=AssertionError("must clarify before probing auth"))
    monkeypatch.setattr(skill, "_probe_cli_state", probe)
    context = SkillContext(user_id="owner", session_id="session", message="我要今天23:00召开视频会议，所有人都参加")
    result = asyncio.run(skill.execute(context))
    assert result.data["requires_input"]
    assert "参会人" in result.message
    probe.assert_not_awaited()
    assert not skill.clarification_for_query(context.message + "\n补充信息：研发群，1小时")
    assert not skill.clarification_for_query(context.message + "\n补充信息：张三、李四，1小时")
    assert skill.clarification_for_query(context.message + "\n补充信息：还是所有人")
    assert skill.clarification_for_query("添加会议日程，明天下午三点开会，参与人：xxx，xxx")


def test_chinese_reference_retrieval_finds_create_and_attendee_docs():
    skill = LarkCLISkill()
    selected = [skill._skills_metadata["lark-calendar"]]
    references = skill._select_references("请创建会议日程并添加参会人", selected)
    names = {Path(ref.path).name for ref in references}
    assert "lark-calendar-create.md" in names
    assert "lark-calendar-schedule-meeting.md" in names
    assert any("attendee" in ref.content for ref in references)


def test_meeting_time_is_not_background_execution_time():
    assert parse_schedule_intent("帮我创建明天23:00的会议，参会人：张三") is None
    assert parse_schedule_intent("创建每天9:00的晨会日程") is None
    assert parse_schedule_intent("定时明天9:00提醒我开会") is not None


def test_message_content_time_is_not_background_execution_time():
    typo_message = "给资料管理群法个消息：明天下午三点开会"
    conversational_message = "麻烦在资料管理群里跟大家知会一声，明天下午三点线上碰一下"
    assert not has_schedule_execution_intent(typo_message)
    assert not has_schedule_execution_intent(conversational_message)
    assert parse_schedule_intent(typo_message) is None
    assert parse_schedule_intent(conversational_message) is None

    scheduled = parse_schedule_intent("明天下午三点给资料管理群发消息：线上开会")
    assert scheduled is not None
    assert scheduled.task_message == "给资料管理群发消息：线上开会"


def test_vague_recurring_schedule_requires_a_concrete_time():
    query = "以后每逢工作日快下班时，帮我看看项目群有没有未回应的阻塞事项"
    clarification = schedule_clarification_for_query(query)
    assert "具体执行时间" in clarification
    assert parse_schedule_intent(query) is None

    scheduled = parse_schedule_intent("每个工作日18:00查看项目群有没有未回应的阻塞事项")
    assert scheduled is not None
    assert scheduled.schedule_type == "weekdays"
    assert scheduled.time_of_day == "18:00"


def test_preview_uses_one_model_call_without_normalization(monkeypatch):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    monkeypatch.setattr(
        skill,
        "_probe_cli_state",
        AsyncMock(return_value=LarkCLIState(installed=True, configured=True, authenticated=True)),
    )
    monkeypatch.setattr(skill, "_select_relevant_skills", lambda query: [])
    monkeypatch.setattr(skill, "_select_references", lambda *args: [])
    monkeypatch.setattr(skill, "_build_heuristic_plan", lambda query: None)
    normalize = AsyncMock(side_effect=AssertionError("preview must not call a second model"))
    monkeypatch.setattr(skill, "_normalize_query_with_agent", normalize)
    planner = AsyncMock(return_value={"summary": "查询结果", "commands": [], "planning_source": "deterministic"})
    monkeypatch.setattr(skill, "_plan_with_llm", planner)
    context = SkillContext(user_id="owner", session_id="", message="查询信息")
    plan = asyncio.run(skill.preview_plan(context, context.message))
    assert plan["summary"] == "查询结果"
    assert plan["planning_source"] == "model"
    normalize.assert_not_awaited()
    planner.assert_awaited_once()
    assert planner.await_args.kwargs["timeout"] == skill._plan_llm_timeout


@pytest.mark.parametrize(
    ("query", "group", "message"),
    [
        ("给「资料管理群」发送通知：今晚八点开会", "资料管理群", "今晚八点开会"),
        ("给【资料管理群】发消息：请提交周报", "资料管理群", "请提交周报"),
        ("给资料管理群发送通知：请提交周报", "资料管理群", "请提交周报"),
        ("在资料管理群里发通知：请提交周报", "资料管理群", "请提交周报"),
        ("通知资料管理群：请提交周报", "资料管理群", "请提交周报"),
        ("给群「齐步走」发送一条消息：集合", "齐步走", "集合"),
        ("给群聊齐步走发送通知：集合", "齐步走", "集合"),
        ("在『英语』群里发个消息：remember the deadline", "英语", "remember the deadline"),
        ("给【项目群】发送通知：第一行\n第二行", "项目群", "第一行\n第二行"),
        ("给资料管理群法个消息：明天下午三点开会", "资料管理群", "明天下午三点开会"),
        ("给资料管理群发个通之：请提交周报", "资料管理群", "请提交周报"),
        (
            "麻烦在资料管理群里跟大家知会一声，明天下午三点线上碰一下",
            "资料管理群",
            "明天下午三点线上碰一下",
        ),
    ],
)
def test_group_notification_variants_use_deterministic_route(query, group, message):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    assert skill._parse_group_message_request(query) == (group, message)
    plan = skill._build_im_send_group_plan(query)
    assert plan and plan["need_confirmation"] is True
    assert f'--query "{group}"' in plan["commands"][0]["command"]
    assert "contact +search-user" not in plan["commands"][0]["command"]


def test_group_notification_route_does_not_capture_people_or_negated_actions():
    skill = LarkCLISkill.__new__(LarkCLISkill)
    assert skill._parse_group_message_request("给张三发送通知：下午开会") is None
    assert skill._parse_direct_message_request("给张三发送通知：下午开会") == ("张三", "下午开会")
    assert skill._parse_group_message_request("给「张三」发消息：你好") is None
    assert skill._parse_direct_message_request("给「张三」发消息：你好") == ("张三", "你好")
    assert skill._parse_group_message_request("不要给资料管理群发送通知：测试") is None
    assert skill._parse_direct_message_request("不要给资料管理群发送通知：测试") is None


def test_group_notification_plan_records_deterministic_source_without_model(monkeypatch):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill._skills_metadata = {}
    monkeypatch.setattr(skill, "_select_relevant_skills", lambda query: [])
    monkeypatch.setattr(skill, "_select_references", lambda *args: [])
    planner = AsyncMock(side_effect=AssertionError("deterministic group notification must not call a model"))
    monkeypatch.setattr(skill, "_plan_with_llm", planner)
    context = SkillContext(user_id="owner", session_id="", message="给「资料管理群」发送通知：测试")
    state = LarkCLIState(installed=True, configured=True, authenticated=True)
    plan, _, _ = asyncio.run(
        skill._plan_commands(context, context.message, state, normalize_intent=False, llm_timeout=1)
    )
    assert plan["planning_source"] == "deterministic"
    assert plan["need_confirmation"] is True
    planner.assert_not_awaited()

    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=state))
    preview = asyncio.run(skill.preview_plan(context, context.message))
    assert preview["need_confirmation"] is True
    assert preview["commands"][0]["write"] is False


def test_action_typo_plan_is_normalized_without_model(monkeypatch):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill._skills_metadata = {}
    monkeypatch.setattr(skill, "_select_relevant_skills", lambda query: [])
    monkeypatch.setattr(skill, "_select_references", lambda *args: [])
    planner = AsyncMock(side_effect=AssertionError("safe local normalization must not call a model"))
    monkeypatch.setattr(skill, "_plan_with_llm", planner)
    query = "给资料管理群法个消息：明天下午三点开会"
    context = SkillContext(user_id="owner", session_id="", message=query)
    state = LarkCLIState(installed=True, configured=True, authenticated=True)
    plan, _, _ = asyncio.run(skill._plan_commands(context, query, state, normalize_intent=False, llm_timeout=1))
    assert plan["planning_source"] == "normalized_deterministic"
    assert plan["normalized_query"] == "给资料管理群发个消息：明天下午三点开会"
    assert plan["corrections"] == ["法个消息→发个消息"]
    assert plan["intent_type"] == "group_message"
    planner.assert_not_awaited()


def test_group_notification_execution_resolves_chat_then_builds_send_command():
    skill = LarkCLISkill.__new__(LarkCLISkill)
    query = "给「资料管理群」发送通知：今晚八点开会"

    first_step = skill._build_heuristic_step(query, [])
    assert first_step and first_step["command"].startswith("lark-cli im +chat-search ")

    search_result = {
        "command": first_step["command"],
        "expected": "search",
        "success": True,
        "stdout": '{"ok":true,"data":{"chats":[{"chat_id":"oc_test_group"}]}}',
        "stderr": "",
    }
    send_step = skill._build_heuristic_step(query, [search_result])
    assert send_step and send_step["expected"] == "write"
    assert '--chat-id "oc_test_group"' in send_step["command"]
    assert '--text "今晚八点开会"' in send_step["command"]

    send_result = {**search_result, "command": send_step["command"]}
    assert skill._build_heuristic_step(query, [search_result, send_result])["done"] is True


def test_plan_timeout_hierarchy_leaves_room_for_model_fallback():
    assert chat.PLAN_PREVIEW_TIMEOUT >= chat._settings.LARK_CLI_PLAN_LLM_TIMEOUT + 2


def test_preview_model_call_disables_transport_retries():
    options = {}

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def with_options(self, **kwargs):
            options.update(kwargs)
            return self

        @staticmethod
        def create(**kwargs):
            message = SimpleNamespace(content='{"summary":"ok","commands":[]}')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.client = FakeClient()
    skill.settings = SimpleNamespace(LLM_PROVIDER="openai", LLM_MODEL="test-model", LARK_CLI_LLM_TIMEOUT=25)
    result = asyncio.run(skill._run_llm_json(system_prompt="system", user_prompt="user", max_tokens=10, timeout=2))
    assert result == {"summary": "ok", "commands": []}
    assert options == {"timeout": 2, "max_retries": 0}


def test_deepseek_preview_disables_reasoning_trace_only_for_bounded_pass():
    request = {}

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def with_options(self, **kwargs):
            return self

        @staticmethod
        def create(**kwargs):
            request.update(kwargs)
            message = SimpleNamespace(content='{"summary":"ok","commands":[]}')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.client = FakeClient()
    skill.settings = SimpleNamespace(
        LLM_PROVIDER="openai",
        LLM_MODEL="deepseek-v4-flash",
        OPENAI_BASE_URL="https://api.deepseek.com",
        LARK_CLI_LLM_TIMEOUT=25,
    )

    result = asyncio.run(
        skill._run_llm_json(system_prompt="system", user_prompt="user", max_tokens=2500, timeout=8)
    )

    assert result == {"summary": "ok", "commands": []}
    assert request["max_tokens"] == 2500
    assert "reasoning_effort" not in request
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}

    request.clear()
    asyncio.run(skill._run_llm_json(system_prompt="system", user_prompt="user", max_tokens=2500))
    assert "extra_body" not in request


def test_group_unanswered_request_retrieves_im_rules_with_compact_prompt():
    query = "请检查项目群最近两天没人回复的阻塞事项，整理成摘要，并给出跟进顺序"
    skill = LarkCLISkill()
    selected = skill._select_relevant_skills(query)
    references = skill._select_references(query, selected)

    assert "lark-im" in [item.key for item in selected]
    assert "lark-mail" not in [item.key for item in selected]
    assert references[0].path.name in {
        "lark-im-chat-messages-list.md",
        "lark-im-messages-search.md",
    }

    messages = skill._build_planning_messages(
        SkillContext(user_id="owner", session_id="", message=query),
        query,
        selected,
        references,
        LarkCLIState(installed=True, configured=True, authenticated=True),
    )
    assert sum(len(item["content"]) for item in messages) < 15_000


def test_explicit_document_preview_needs_no_model():
    skill = LarkCLISkill.__new__(LarkCLISkill)
    target = "Kdqvd2pNboqlmRxdeXrcD4N4nJb"
    for query in [f"读取测试文档 {target}", f"读取 https://example.feishu.cn/docx/{target}，只读取，不修改。"]:
        plan = skill._build_doc_read_plan(query)
        assert plan and not plan["need_confirmation"]
        assert target in plan["commands"][0]["command"]
    assert skill._build_doc_read_plan(f"读取文档 {target} 并删除") is None
    assert skill._build_doc_read_plan("读取我昨天的文档") is None


def test_schedule_preview_skips_model_and_slow_preview_times_out(database, monkeypatch):
    class SlowSkill:
        clarification_for_query = staticmethod(lambda query: "")

        async def preview_plan(self, *args):
            await asyncio.sleep(30)

    monkeypatch.setattr(chat, "LarkCLISkill", SlowSkill)
    monkeypatch.setattr(chat, "PLAN_PREVIEW_TIMEOUT", 0.01)
    monkeypatch.setattr(chat.scheduled_task_config_store, "enabled", lambda: True)
    account = AccountInfo(account="owner", name="Owner")
    result = asyncio.run(chat.preview_chat_plan(chat.PlanPreviewRequest(message="定时明天9:00读取文档"), account))
    assert result["data"]["plan"]["intent_type"] == "scheduled_task"
    with pytest.raises(HTTPException) as error:
        asyncio.run(chat.preview_chat_plan(chat.PlanPreviewRequest(message="查询测试信息"), account))
    assert error.value.status_code == 504
