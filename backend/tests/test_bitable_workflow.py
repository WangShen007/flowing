"""Simulated Base field discovery, confirmed update, readback, and ACL tests.

The executor below is deliberately synthetic: it speaks the installed CLI's
field/record response shapes but never contacts Feishu.  The test still runs
the production model-driven workflow, command catalog, approval checkpoint,
and post-write readback path.
"""

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


ROLE_NAMES = ("employee", "lead", "admin")


def response(name: str, args: dict, call_id: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }


class SyntheticBitable:
    """Small API-shaped fake with a read-only single-select status field."""

    fields = [
        {"field_id": "fld_title", "field_name": "Title", "type": "text"},
        {
            "field_id": "fld_status",
            "field_name": "Status",
            "type": "singleSelect",
            "property": {
                "options": [
                    {"id": "opt_todo", "name": "Todo"},
                    {"id": "opt_done", "name": "Done"},
                ],
                "multiple": False,
            },
        },
    ]

    def __init__(self, *, role: str, deny_writes: bool = False) -> None:
        self.role = role
        self.deny_writes = deny_writes
        self.status = ["Todo"]
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, command: str, *args, **kwargs):
        del args
        parts = shlex.split(command)
        assert parts[0] == "lark-cli"
        assert parts[-4:] == ["--as", "user", "--format", "json"]
        path = tuple(parts[1:3])
        self.calls.append((command, kwargs))

        common = parts[parts.index("--base-token") + 1], parts[parts.index("--table-id") + 1]
        assert common == ("app_demo", "tbl_tasks")

        if path == ("base", "+field-list"):
            assert kwargs["approved_write"] is False
            return True, json.dumps({
                "code": 0,
                "data": {"total": len(self.fields), "fields": self.fields},
            }, ensure_ascii=False), ""

        if path == ("base", "+record-batch-update"):
            assert kwargs["approved_write"] is True
            payload = json.loads(parts[parts.index("--json") + 1])
            update = payload["update_records"]["rec_task"]
            status = update.get("Status", update.get("fld_status"))
            if self.deny_writes:
                return False, "", (
                    f"resource_acl_denied: base/table is not writable for role {self.role}"
                )
            assert status == ["Done"]
            self.status = status
            return True, json.dumps({"code": 0, "data": {"ignored_fields": []}}), ""

        if path == ("base", "+record-list"):
            assert kwargs["approved_write"] is False
            return True, json.dumps({
                "code": 0,
                "data": {
                    # This is the raw matrix shape emitted by the official
                    # record-list shortcut, rather than a made-up row shape.
                    "fields": ["Status"],
                    "field_id_list": ["fld_status"],
                    "record_id_list": ["rec_task"],
                    "data": [self.status],
                    "total": 1,
                },
            }), ""

        raise AssertionError(f"unexpected command: {command}")


@pytest.fixture
def make_bitable_harness(tmp_path, monkeypatch):
    from app.core import feishu_permissions
    from app.skills.lark_cli import tool_catalog

    monkeypatch.setattr(graph, "DATA_DIR", tmp_path)
    database = SQLiteStore(tmp_path / "app.sqlite3")
    for module in (graph, feishu_permissions, tool_catalog):
        monkeypatch.setattr(module, "store", database)
    database.execute("INSERT INTO accounts VALUES ('owner', 'Owner', '', 1, 1)")
    monkeypatch.setattr(graph.workflow_memory_store, "list_for_user", lambda *_: [])
    monkeypatch.setattr(graph.feishu_tokens, "enterprise_configured", lambda: False)

    def factory(role: str = "employee"):
        database.execute(
            "INSERT INTO account_memberships(account,role,updated_at) VALUES ('owner',?,1)",
            (role,),
        )
        skill = SimpleNamespace(
            settings=Settings(),
            execute_command=AsyncMock(),
            _make_progress_update=lambda text: {"type": "progress", "content": text},
            _build_scope_setup_metadata=lambda state, scopes: {
                "setup_required": True,
                "setup_scopes": scopes,
            },
        )
        context = SkillContext(
            user_id="owner",
            session_id=f"bitable-{role}",
            message="把任务 rec_task 的 Status 标记为 Done，并核验结果",
        )
        return skill, context

    return factory


async def run(skill, context):
    events = [event async for event in graph.execute_agent(skill, context, context.message, None)]
    return events[-1]["result"]


def _tool_args(operation: str, arguments: dict) -> dict:
    return {"operation": operation, "arguments": arguments}


def _workflow_responses(*, finish_outcome: str = "completed") -> list[dict]:
    field_list = _tool_args(
        "base +field-list",
        {"base-token": "app_demo", "table-id": "tbl_tasks"},
    )
    update = _tool_args(
        "base +record-batch-update",
        {
            "base-token": "app_demo",
            "table-id": "tbl_tasks",
            "json": json.dumps(
                {"update_records": {"rec_task": {"Status": ["Done"]}}},
                ensure_ascii=False,
            ),
        },
    )
    readback = _tool_args(
        "base +record-list",
        {"base-token": "app_demo", "table-id": "tbl_tasks"},
    )
    return [
        response("describe_tool", {"operation": "base +field-list"}, "describe-fields"),
        response("invoke_tool", field_list, "read-fields"),
        response("describe_tool", {"operation": "base +record-batch-update"}, "describe-update"),
        response("invoke_tool", update, "update-status"),
        response("describe_tool", {"operation": "base +record-list"}, "describe-readback"),
        response("invoke_tool", readback, "readback-status"),
        response(
            "finish_task",
            {
                "answer": "已把 Status 标记为 Done，并通过回读确认。",
                "outcome": finish_outcome,
                "experience": "先读取字段类型，再按对应 CellValue 更新并回读验证。",
            },
            "finish",
        ),
    ]


def test_bitable_schema_confirmed_single_select_update_and_readback(make_bitable_harness, monkeypatch):
    """Use real installed help schemas and require approval before the write."""

    skill, context = make_bitable_harness("employee")
    from app.skills.lark_cli import tool_catalog

    field_description = asyncio.run(tool_catalog.describe_operation("base +field-list"))
    update_description = asyncio.run(tool_catalog.describe_operation("base +record-batch-update"))
    assert {"base-token", "table-id"} <= set(field_description["input_schema"]["properties"])
    assert update_description["input_schema"]["properties"]["json"]["type"] == "string"

    async def describe(operation):
        return await tool_catalog.describe_operation(operation)

    monkeypatch.setattr(graph, "describe_operation", describe)
    fake = SyntheticBitable(role="employee")
    skill.execute_command = AsyncMock(side_effect=fake.execute)
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=_workflow_responses()))

    first = asyncio.run(run(skill, context))
    assert first.data["approval_required"] is True
    assert skill.execute_command.await_count == 1
    assert fake.calls[0][0].startswith("lark-cli base +field-list")
    assert first.data["pending_tool"]["operation"] == "base +record-batch-update"
    pending = shlex.split(first.data["pending_tool"]["command"])
    assert json.loads(pending[pending.index("--json") + 1]) == {
        "update_records": {"rec_task": {"Status": ["Done"]}},
    }

    context.metadata = {
        "resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]},
        "approve_tool": True,
    }
    final = asyncio.run(run(skill, context))
    assert final.success is True
    assert final.data["run_state"] == "completed"
    assert "Done" in final.message
    assert len(fake.calls) == 3
    assert fake.status == ["Done"]
    assert fake.calls[1][1]["approved_write"] is True
    assert fake.calls[2][0].startswith("lark-cli base +record-list")
    assert all(record["success"] for record in final.data["executed_commands"])


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_resource_acl_denial_cannot_be_overridden_by_role(make_bitable_harness, monkeypatch, role):
    """Employee/lead/admin all stop on a resource write denial after approval."""

    skill, context = make_bitable_harness(role)
    from app.skills.lark_cli import tool_catalog

    async def describe(operation):
        return await tool_catalog.describe_operation(operation)

    monkeypatch.setattr(graph, "describe_operation", describe)
    fake = SyntheticBitable(role=role, deny_writes=True)
    skill.execute_command = AsyncMock(side_effect=fake.execute)
    # The model may still perform a readback after a rejected write, but that
    # readback must not turn the failed mutation into a success claim.
    calls = _workflow_responses()
    # Try to claim completion after the denied write.  The graph must reject
    # that claim; the final failed call records the truthful outcome.
    calls.append(response(
        "finish_task",
        {
            "answer": "资源权限拒绝，Status 未更新。",
            "outcome": "completed",
            "experience": "",
        },
        "finish-invalid",
    ))
    calls.append(response(
        "finish_task",
        {
            "answer": "资源权限拒绝，Status 未更新。",
            "outcome": "failed",
            "experience": "",
        },
        "finish-failed",
    ))
    monkeypatch.setattr(graph, "call_model", AsyncMock(side_effect=calls))

    first = asyncio.run(run(skill, context))
    assert first.data["approval_required"] is True
    context.metadata = {
        "resume_checkpoint": {"agent_run_id": first.data["agent_run_id"]},
        "approve_tool": True,
    }
    final = asyncio.run(run(skill, context))
    assert final.success is False
    assert final.data["run_state"] == "failed"
    assert len(fake.calls) == 3
    assert fake.calls[1][1]["approved_write"] is True
    assert fake.calls[2][1]["approved_write"] is False
    assert fake.status == ["Todo"]
    denied = next(record for record in final.data["executed_commands"] if record["expected"] == "write")
    assert denied["success"] is False
    assert denied["expected"] == "write"
    assert "resource_acl_denied" in denied["stderr"]
