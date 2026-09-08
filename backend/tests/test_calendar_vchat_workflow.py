"""Contract and simulated lifecycle tests for user-owned calendar VC events."""

from __future__ import annotations

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


def response(name, args, call_id):
    return {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}


@pytest.fixture
def calendar_harness(tmp_path, monkeypatch):
    from app.core import feishu_permissions
    from app.skills.lark_cli import tool_catalog

    monkeypatch.setattr(graph, "DATA_DIR", tmp_path)
    database = SQLiteStore(tmp_path / "app.sqlite3")
    for module in (graph, feishu_permissions, tool_catalog):
        monkeypatch.setattr(module, "store", database)
    database.execute("INSERT INTO accounts VALUES ('owner', 'Owner', '', 1, 1)")
    database.execute("INSERT INTO account_memberships(account,role,updated_at) VALUES ('owner','employee',1)")
    monkeypatch.setattr(graph.workflow_memory_store, "list_for_user", lambda *_: [])
    monkeypatch.setattr(graph.feishu_tokens, "enterprise_configured", lambda: False)
    skill = SimpleNamespace(
        settings=Settings(),
        execute_command=AsyncMock(return_value=(True, '{"ok":true}', "")),
        _make_progress_update=lambda text: {"type": "progress", "content": text},
        _build_scope_setup_metadata=lambda state, scopes: {"setup_required": True, "setup_scopes": scopes},
    )
    context = SkillContext(user_id="owner", session_id="calendar-vchat", message="创建视频会议")
    return skill, context


async def run(skill, context):
    events = [event async for event in graph.execute_agent(skill, context, context.message, None)]
    return events[-1]["result"]


def test_native_calendar_vchat_create_approval_and_readback(calendar_harness, monkeypatch):
    """Create with user identity, approve once, then read the API-shaped meeting URL."""

    skill, context = calendar_harness
    from app.skills.lark_cli import tool_catalog

    async def describe(operation):
        return await tool_catalog.describe_operation(operation)

    monkeypatch.setattr(graph, "describe_operation", describe)
    create_args = {
        "operation": "calendar events create",
        "arguments": {
            "params": {"calendar_id": "cal_user_primary"},
            "data": {
                "summary": "评审视频会议",
                "start_time": {"datetime": "2030-01-01T07:00:00", "timezone": "Asia/Shanghai"},
                "end_time": {"datetime": "2030-01-01T08:00:00", "timezone": "Asia/Shanghai"},
                "vchat": {"vc_type": "vc"},
            },
        },
    }
    get_args = {
        "operation": "calendar events get",
        "arguments": {
            "params": {"calendar_id": "cal_user_primary", "event_id": "evt_user_vc"},
        },
    }
    meeting_url = "https://meetings.feishu.cn/s/eval-user-vc"
    calls = []

    async def fake_execute(command, *args, **kwargs):
        calls.append((command, kwargs))
        parts = shlex.split(command)
        if parts[1:4] == ["calendar", "events", "create"]:
            params = json.loads(parts[parts.index("--params") + 1])
            data = json.loads(parts[parts.index("--data") + 1])
            assert params == {"calendar_id": "cal_user_primary"}
            assert data["vchat"] == {"vc_type": "vc"}
            assert kwargs["approved_write"] is True
            assert parts[parts.index("--as") + 1] == "user"
        elif parts[1:4] == ["calendar", "events", "get"]:
            params = json.loads(parts[parts.index("--params") + 1])
            assert params == {"calendar_id": "cal_user_primary", "event_id": "evt_user_vc"}
            assert kwargs["approved_write"] is False
        else:
            raise AssertionError(f"unexpected command: {command}")
        event = {
            "event_id": "evt_user_vc",
            "summary": "评审视频会议",
            "vchat": {"vc_type": "vc", "meeting_url": meeting_url},
        }
        return True, json.dumps({"code": 0, "msg": "ok", "data": {"event": event}}, ensure_ascii=False), ""

    skill.execute_command = AsyncMock(side_effect=fake_execute)
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=[
        response("describe_tool", {"operation": create_args["operation"]}, "create-desc"),
        response("invoke_tool", create_args, "create"),
        response("describe_tool", {"operation": get_args["operation"]}, "get-desc"),
        response("invoke_tool", get_args, "get"),
        response("finish_task", {"answer": f"已创建并核验视频会议：{meeting_url}",
                                  "outcome": "completed", "experience": ""}, "done"),
    ]))

    first = asyncio.run(run(skill, context))
    assert first.data["approval_required"]
    assert skill.execute_command.await_count == 0
    pending_parts = shlex.split(first.data["pending_tool"]["command"])
    assert json.loads(pending_parts[pending_parts.index("--data") + 1])["vchat"] == {"vc_type": "vc"}
    assert pending_parts[pending_parts.index("--as") + 1] == "user"

    context.metadata = {"resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]}, "approve_tool": True}
    final = asyncio.run(run(skill, context))
    assert final.success
    assert final.message.endswith(meeting_url)
    assert len(calls) == 2
    assert meeting_url in final.data["executed_commands"][0]["stdout"]
    assert meeting_url in final.data["executed_commands"][1]["stdout"]
