import asyncio
import json
import shlex
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.core.storage import SQLiteStore
from app.skills.base import SkillContext
from app.skills.lark_cli import agent_graph as graph
from app.skills.lark_cli.tool_catalog import build_command


def response(name, args, call_id):
    return {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}


def desc(operation):
    return {"operation": operation, "input_schema": {"type": "object", "properties": {
        "query": {"type": "string"}, "chat-id": {"type": "string"}, "text": {"type": "string"}},
        "additionalProperties": False}}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    from app.core import account_access, feishu_permissions
    from app.skills.lark_cli import tool_catalog

    monkeypatch.setattr(graph, "DATA_DIR", tmp_path)
    database = SQLiteStore(tmp_path / "app.sqlite3")
    for module in (graph, feishu_permissions, tool_catalog, account_access):
        monkeypatch.setattr(module, "store", database)
    database.execute("INSERT INTO accounts VALUES ('owner', 'Owner', '', 1, 1)")
    database.execute("INSERT INTO account_memberships(account,role,updated_at) VALUES ('owner','employee',1)")
    monkeypatch.setattr(graph, "describe_operation", AsyncMock(side_effect=lambda op: desc(op)))
    monkeypatch.setattr(graph.workflow_memory_store, "list_for_user", lambda *_: [])
    monkeypatch.setattr(graph.feishu_tokens, "enterprise_configured", lambda: False)
    skill = SimpleNamespace(settings=Settings(), execute_command=AsyncMock(return_value=(True, '{"count":12}', '')),
                            _make_progress_update=lambda text: {"type": "progress", "content": text},
                            _build_scope_setup_metadata=lambda state, scopes: {"setup_required": True, "setup_scopes": scopes})
    context = SkillContext(user_id="owner", session_id="conversation", message="看看刚才那个群有多少人")
    return skill, context


async def run(skill, context):
    events = [event async for event in graph.execute_agent(skill, context, context.message, None)]
    return events[-1]["result"]


def test_observation_drives_next_tool_without_keyword_route(harness, monkeypatch):
    skill, context = harness
    replies = [response("describe_tool", {"operation": "im +chat-search"}, "a"),
               response("invoke_tool", {"operation": "im +chat-search", "arguments": {"query": "齐步走"}}, "b"),
               response("finish_task", {"answer": "查到12人", "outcome": "completed", "experience": ""}, "c")]
    model = AsyncMock(side_effect=replies)
    monkeypatch.setattr(graph, "call_model", model)
    result = asyncio.run(run(skill, context))
    assert result.success
    assert skill.execute_command.await_count == 1
    messages = model.call_args_list[-1].args[1]
    assert 'count' in messages[-1]["content"]
    assert "当前操作目录" in messages[0]["content"] and "im +chat-search" in messages[0]["content"]
    assert result.data["plan"]["planning_source"] == "langgraph"
    timings = result.data["plan"]["timings"]
    assert [item["stage"] for item in timings] == ["model", "describe_tool", "model", "invoke_tool", "model", "finish_task"]
    assert all(item["duration_ms"] >= 0 for item in timings)
    assert all("content" not in item and "arguments" not in item for item in timings)


def test_fake_confirmation_finish_is_rejected_and_real_tool_can_resume(harness, monkeypatch):
    skill, context = harness
    model = AsyncMock(side_effect=[
        response("finish_task", {"answer": "已纠正，等待确认", "outcome": "clarification", "experience": ""}, "bad"),
        response("describe_tool", {"operation": "im +messages-send"}, "describe"),
        response("invoke_tool", {"operation": "im +messages-send", "arguments": {"chat-id": "oc_test", "text": "test"}}, "write")])
    monkeypatch.setattr(graph, "call_model", model)
    result = asyncio.run(run(skill, context))
    assert result.data["approval_required"]
    assert "clarification 必须" in model.call_args_list[1].args[1][-1]["content"]
    skill.execute_command.assert_not_awaited()


def test_real_clarification_shows_question_not_fake_plan(harness, monkeypatch):
    skill, context = harness
    monkeypatch.setattr(graph, "call_model", AsyncMock(return_value=response("finish_task", {
        "answer": "已生成计划", "question": "具体是哪一条记录？", "outcome": "clarification", "experience": ""}, "ask")))
    result = asyncio.run(run(skill, context))
    assert "具体是哪一条记录" in result.message
    assert "已生成计划" not in result.message


def test_repeated_describe_reuses_current_run_schema(harness, monkeypatch):
    skill, context = harness
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("describe_tool", {"operation": "im +chat-search"}, "a"),
        response("describe_tool", {"operation": "im +chat-search"}, "b"),
        response("finish_task", {"answer": "", "question": "查询哪个群？", "outcome": "clarification", "experience": ""}, "c"),
        response("finish_task", {"answer": "查询哪个群？", "question": "查询哪个群？", "outcome": "clarification", "experience": ""}, "d")]))
    asyncio.run(run(skill, context))
    assert graph.describe_operation.await_count == 1


def test_business_resource_requires_confirmation_and_verifies_fields(harness, monkeypatch):
    from app.core.business_resources import list_resources

    skill, context = harness
    config = {"action": "save", "alias": "项目表", "base_token": "BaseToken12345", "table_id": "tblProjects", "fields": {"状态": "fldStatus"}}
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("business_resources", config, "save"),
        response("finish_task", {"answer": "已保存配置", "outcome": "completed", "experience": ""}, "done")]))
    skill.execute_command.return_value = (True, json.dumps({"ok": True, "data": {"fields": [
        {"id": "fldStatus", "name": "状态", "type": "select"}]}}), "")
    first = asyncio.run(run(skill, context))
    assert first.data["approval_required"]
    assert list_resources(graph.store, context.user_id) == []
    skill.execute_command.assert_not_awaited()
    context.metadata = {"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}, "approve_tool": True}
    second = asyncio.run(run(skill, context))
    assert second.success
    assert list_resources(graph.store, context.user_id)[0]["alias"] == "项目表"
    assert skill.execute_command.await_count == 1


def test_resource_list_is_current_account_only(harness, monkeypatch):
    skill, context = harness
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("business_resources", {"action": "list"}, "list"),
        response("finish_task", {"answer": "没有配置", "outcome": "completed", "experience": ""}, "done")]))
    result = asyncio.run(run(skill, context))
    assert result.success
    skill.execute_command.assert_not_awaited()


def test_resource_save_rejects_changed_schema_after_approval(harness, monkeypatch):
    from app.core.business_resources import list_resources

    skill, context = harness
    model = AsyncMock(side_effect=[
        response("business_resources", {"action": "save", "alias": "项目表", "base_token": "BaseToken12345",
                                       "table_id": "tblProjects", "fields": {"状态": "fldGone"}}, "save"),
        response("finish_task", {"answer": "映射失效，未保存", "outcome": "failed", "experience": ""}, "done")])
    monkeypatch.setattr(graph, "call_model", model)
    first = asyncio.run(run(skill, context))
    skill.execute_command.return_value = (True, json.dumps({"ok": True, "data": {"fields": [
        {"id": "fldNew", "name": "状态", "type": "select"}]}}), "")
    context.metadata = {"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}, "approve_tool": True}
    result = asyncio.run(run(skill, context))
    assert not result.success
    assert list_resources(graph.store, context.user_id) == []
    assert "映射已失效" in model.call_args.args[1][-1]["content"]


def test_write_interrupt_is_durable_and_resumes_exact_call(harness, monkeypatch):
    skill, context = harness
    replies = [response("describe_tool", {"operation": "im +messages-send"}, "a"),
               response("invoke_tool", {"operation": "im +messages-send", "arguments": {"chat-id": "oc_real", "text": "确定的正文"}}, "b"),
               response("finish_task", {"answer": "已发送", "outcome": "completed", "experience": ""}, "c")]
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=replies))
    first = asyncio.run(run(skill, context))
    assert first.data["approval_required"]
    approved_command = first.data["pending_tool"]["command"]
    assert "--idempotency-key web-" in approved_command
    assert skill.execute_command.await_count == 0
    context.metadata = {"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}, "approve_tool": True}
    # New graph, new saver connection, new event loop: a real persisted resume.
    second = asyncio.run(run(skill, context))
    assert second.success
    assert skill.execute_command.await_count == 1
    assert "确定的正文" in skill.execute_command.call_args.args[0]
    assert skill.execute_command.call_args.args[0] == approved_command
    assert sum(item["stage"] == "invoke_tool" for item in second.data["plan"]["timings"]) == 1
    third = asyncio.run(run(skill, context))
    assert not third.success
    assert skill.execute_command.await_count == 1


def test_authorization_pauses_before_execution(harness, monkeypatch):
    skill, context = harness
    monkeypatch.setattr(graph.feishu_tokens, "enterprise_configured", lambda: True)
    permissions = ["im:chat.members:read"]
    monkeypatch.setattr(graph, "check_command_permission", lambda *_, **kwargs: permissions.copy())
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("describe_tool", {"operation": "im +chat-members-list"}, "a"),
        response("invoke_tool", {"operation": "im +chat-members-list", "arguments": {"chat-id": "oc_real"}}, "b"),
        response("request_authorization", {"operation": "im +chat-members-list"}, "auth"),
        response("invoke_tool", {"operation": "im +chat-members-list", "arguments": {"chat-id": "oc_real"}}, "retry"),
        response("finish_task", {"answer": "12人", "outcome": "completed", "experience": ""}, "c")]))
    first = asyncio.run(run(skill, context))
    assert first.data["setup_required"]
    assert skill.execute_command.await_count == 0
    permissions.clear()
    context.metadata = {"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}}
    assert asyncio.run(run(skill, context)).success
    assert skill.execute_command.await_count == 1


def test_tool_schema_rejects_unknown_flags_and_preserves_shell_literals():
    description = desc("im +chat-search")
    with pytest.raises(ValueError):
        build_command("im +chat-search", {"profile": "other"}, description)
    with pytest.raises(ValueError):
        build_command("im +invented", {}, description)
    import shlex
    command = build_command("im +chat-search", {"query": '群; $(touch /tmp/never)'}, description)
    assert shlex.split(command)[4] == '群; $(touch /tmp/never)'


def test_text_mentions_are_validated_before_confirmation():
    description = {"operation": "im +messages-send", "input_schema": {"type": "object"}}
    good = {"chat-id": "oc_test", "msg-type": "text",
            "content": json.dumps({"text": '<at user_id="ou_real">darling</at> 该吃饭了'})}
    assert "messages-send" in build_command("im +messages-send", good, description)
    bad = {**good, "content": json.dumps({"text": '<at user_id=\\"ou_real\\"></at> 该吃饭了'})}
    with pytest.raises(ValueError, match="提及格式错误"):
        build_command("im +messages-send", bad, description)


def _message_content(command):
    args = shlex.split(command)
    return json.loads(args[args.index("--content") + 1])


def test_post_content_unwraps_model_wrapper_and_normalizes_mentions():
    description = {"operation": "im +messages-send", "input_schema": {"type": "object"}}
    wrapped = {
        "post": {
            "zh_cn": {
                "title": "通知",
                "content": [[{"tag": "text", "text": '<at open_id="ou_real">小林</at> 请查看'}]],
            }
        }
    }
    command = build_command(
        "im +messages-send",
        {"chat-id": "oc_test", "msg-type": "post", "content": json.dumps(wrapped)},
        description,
    )
    content = _message_content(command)
    assert "post" not in content
    assert content["zh_cn"]["content"][0][0]["text"] == '<at user_id="ou_real">小林</at> 请查看'


@pytest.mark.parametrize(
    "mention",
    [
        '<at id=ou_real/>小林',
        '<at open_id="ou_real">小林</at>',
        '<at user_id=ou_real />小林',
    ],
)
def test_official_mention_variants_are_normalized(mention):
    description = {"operation": "im +messages-send", "input_schema": {"type": "object"}}
    command = build_command(
        "im +messages-send",
        {"chat-id": "oc_test", "msg-type": "text", "content": json.dumps({"text": mention})},
        description,
    )
    assert _message_content(command)["text"].startswith('<at user_id="ou_real">')


@pytest.mark.parametrize(
    "content",
    [
        {"post": {"zh_cn": {"content": "not rows"}}},
        {"zh_cn": {"content": [["not a node"]]}},
        {"post": {"zh_cn": {"content": [[{"tag": "text", "text": '<at user_id=\\"ou_real\\">坏</at>'}]]}}},
    ],
)
def test_malformed_post_content_is_rejected(content):
    description = {"operation": "im +messages-send", "input_schema": {"type": "object"}}
    with pytest.raises(ValueError):
        build_command(
            "im +messages-send",
            {"chat-id": "oc_test", "msg-type": "post", "content": json.dumps(content)},
            description,
        )


def test_failed_send_does_not_ask_for_same_approval_again(harness, monkeypatch):
    skill, context = harness
    skill.execute_command.return_value = (False, "", "missing_scope")
    args = {"operation": "im +messages-send", "arguments": {"chat-id": "oc_real", "text": "测试"}}
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("describe_tool", {"operation": args["operation"]}, "describe"),
        response("invoke_tool", args, "send1"), response("invoke_tool", args, "send2"),
        response("finish_task", {"answer": "发送失败，未重发", "outcome": "failed", "experience": ""}, "end")]))
    first = asyncio.run(run(skill, context))
    assert first.data["run_state"] == "waiting_approval"
    context.metadata = {"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}, "approve_tool": True}
    final = asyncio.run(run(skill, context))
    assert not final.success and final.data["run_state"] == "failed"
    assert not final.data.get("approval_required")
    assert skill.execute_command.await_count == 1


def test_provider_failure_not_reported_as_ambiguous_user_request(harness, monkeypatch):
    skill, context = harness
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=TimeoutError("provider timeout")))
    result = asyncio.run(run(skill, context))
    assert not result.success
    assert result.data["run_state"] == "service_error"
    assert not result.data.get("requires_input")
    assert skill.execute_command.await_count == 0


def test_other_account_cannot_resume_pending_write(harness, monkeypatch):
    skill, context = harness
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("describe_tool", {"operation": "im +messages-send"}, "a"),
        response("invoke_tool", {"operation": "im +messages-send", "arguments": {"chat-id": "oc_real", "text": "通知"}}, "b")]))
    first = asyncio.run(run(skill, context))
    other = SkillContext(user_id="other", session_id=context.session_id, message=context.message,
                         metadata={"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}, "approve_tool": True})
    assert not asyncio.run(run(skill, other)).success
    assert skill.execute_command.await_count == 0


def test_schedule_is_confirmed_and_created_only_once(harness, monkeypatch):
    from app.core import scheduled_tasks
    skill, context = harness
    monkeypatch.setattr(scheduled_tasks.scheduled_task_config_store, "enabled", lambda: True)
    created = []

    def create(**kwargs):
        created.append(kwargs)
        return {"id": 123, "task_message": kwargs["intent"].task_message}

    monkeypatch.setattr(scheduled_tasks.scheduled_task_store, "add", create)
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("create_schedule", {"task": "查询项目群人数", "run_at": "2099-01-01T15:00:00+08:00", "recurrence": "once"}, "a"),
        response("finish_task", {"answer": "已安排", "outcome": "completed", "experience": ""}, "b")]))
    first = asyncio.run(run(skill, context))
    assert first.data["approval_required"] and not created
    context.metadata = {"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}, "approve_tool": True}
    assert asyncio.run(run(skill, context)).success
    assert len(created) == 1
    assert not asyncio.run(run(skill, context)).success
    assert len(created) == 1


@pytest.mark.parametrize("persisted_unknown", [True, False, "cli_timeout"])
def test_unknown_write_after_successful_read_cannot_be_reported_completed(harness, monkeypatch, persisted_unknown):
    skill, context = harness
    model = AsyncMock(side_effect=[
        response("describe_tool", {"operation": "im +chat-search"}, "d1"),
        response("invoke_tool", {"operation": "im +chat-search", "arguments": {"query": "项目"}}, "read"),
        response("describe_tool", {"operation": "im +messages-send"}, "d2"),
        response("invoke_tool", {"operation": "im +messages-send", "arguments": {"chat-id": "oc_real", "text": "通知"}}, "write"),
        response("finish_task", {"answer": "全部完成", "outcome": "completed", "experience": ""}, "wrong"),
        response("finish_task", {"answer": "已找到群；发送结果不明，请核实。", "outcome": "failed", "experience": ""}, "honest"),
    ])
    monkeypatch.setattr(graph, "call_model", model)
    skill.execute_command.return_value = (True, '{"chat_id":"oc_real"}', "")
    pending = asyncio.run(run(skill, context))
    # Crash boundary: a started side-effect receipt survived, but no completion
    # was persisted. The graph still has its pending write checkpoint.
    if persisted_unknown is True:
        graph.store.execute("INSERT INTO agent_tool_receipts(run_id,call_id,status) VALUES (?,?,'started')",
                            (pending.data["agent_run_id"], "write"))
    elif persisted_unknown == "cli_timeout":
        skill.execute_command.return_value = (False, "", "execution_result_unknown: 命令执行超时")
    else:
        skill.execute_command.side_effect = TimeoutError("connection lost after remote send")
    context.metadata = {"resume_checkpoint": {"agent_run_id": pending.data["agent_run_id"]}, "approve_tool": True}
    recovered = asyncio.run(run(skill, context))
    assert recovered.data["run_state"] == "failed"
    assert not recovered.success
    assert skill.execute_command.await_count == (1 if persisted_unknown is True else 2)
    records = recovered.data["executed_commands"]
    assert records[0]["success"] is True
    assert records[-1]["status"] == "unknown"
    assert records[-1]["expected"] == "write"
    assert "不能报告全部完成" in model.call_args_list[-1].args[1][-1]["content"]


def test_provider_retry_does_not_replay_successful_read(harness, monkeypatch):
    skill, context = harness
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("describe_tool", {"operation": "im +chat-search"}, "a"),
        response("invoke_tool", {"operation": "im +chat-search", "arguments": {"query": "齐步走"}}, "b"),
        TimeoutError("service temporarily down"),
        response("finish_task", {"answer": "查到结果", "outcome": "completed", "experience": ""}, "c")]))
    first = asyncio.run(run(skill, context))
    assert first.data["run_state"] == "service_error"
    assert skill.execute_command.await_count == 1
    context.metadata = {"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}}
    assert asyncio.run(run(skill, context)).success
    assert skill.execute_command.await_count == 1


def test_each_write_in_multi_action_task_requires_its_own_approval(harness, monkeypatch):
    skill, context = harness
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("describe_tool", {"operation": "im +messages-send"}, "describe"),
        response("invoke_tool", {"operation": "im +messages-send", "arguments": {"chat-id": "oc_first", "text": "第一条"}}, "first"),
        response("invoke_tool", {"operation": "im +messages-send", "arguments": {"chat-id": "oc_second", "text": "第二条"}}, "second"),
        response("finish_task", {"answer": "两条已发送", "outcome": "completed", "experience": ""}, "done"),
    ]))
    first = asyncio.run(run(skill, context))
    assert first.data["pending_tool"]["arguments"]["chat-id"] == "oc_first"
    assert skill.execute_command.await_count == 0
    context.metadata = {"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}, "approve_tool": True}
    second = asyncio.run(run(skill, context))
    assert second.data["pending_tool"]["arguments"]["chat-id"] == "oc_second"
    assert skill.execute_command.await_count == 1
    done = asyncio.run(run(skill, context))
    assert done.success
    assert skill.execute_command.await_count == 2
    commands = [call.args[0] for call in skill.execute_command.call_args_list]
    assert sum("oc_first" in command for command in commands) == 1
    assert sum("oc_second" in command for command in commands) == 1


def test_default_preview_never_requires_fixed_phrase_or_model_plan(monkeypatch):
    from app.skills.lark_cli.skill_runtime import LarkCLISkill
    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.settings = Settings(LARK_AGENT_ENGINE="langgraph")
    context = SkillContext(user_id="owner", session_id="session", message="齐步走有几个人")
    plan = asyncio.run(skill.preview_plan(context, context.message))
    assert plan["planning_source"] == "langgraph"
    assert not plan["requires_input"] and not plan["need_confirmation"]
    assert plan["commands"] == []
