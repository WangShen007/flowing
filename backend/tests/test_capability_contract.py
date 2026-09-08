"""Static contracts for the reviewed Feishu capability surface.

These tests intentionally cover the reviewed allow-list, not every command exposed
by ``lark-cli``.  Adding a new Feishu operation must therefore update the policy,
scope mapping, role matrix, and verification matrix together.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import subprocess

import pytest

from app.core.feishu_permissions import (
    CAPABILITIES,
    COMMANDS,
    COMMAND_SCOPES,
    CONDITIONAL_COMMAND_SCOPES,
    capabilities_for_role,
    command_capabilities,
    required_command_scopes,
    scopes_for_role,
)
from app.skills.lark_cli.tool_catalog import READ_OPERATIONS, build_command, describe_operation, is_write, operation_key


ROLE_NAMES = ("employee", "lead", "admin")


def test_every_reviewed_operation_has_a_capability_contract() -> None:
    """The model catalog, role policy, and scope policy must share one registry."""

    for path, capabilities in COMMANDS.items():
        assert path and all(path), path
        assert all(capability in CAPABILITIES for capability in capabilities), path
        assert tuple(command_capabilities(["lark-cli", *path])) == capabilities

        # ``drive +task_result`` only polls an already-created asynchronous result;
        # it is deliberately capability-free. Every other operation needs a scope.
        required = required_command_scopes(["lark-cli", *path], capabilities)
        if path != ("drive", "+task_result"):
            assert required, path

    for path in COMMAND_SCOPES:
        assert path in COMMANDS, path
    for path in CONDITIONAL_COMMAND_SCOPES:
        assert path in COMMANDS, path


def test_role_matrix_is_monotonic_and_lead_only_capabilities_are_explicit() -> None:
    employee = set(capabilities_for_role("employee"))
    lead = set(capabilities_for_role("lead"))
    admin = set(capabilities_for_role("admin"))

    assert employee <= lead <= admin
    assert {"groups_create", "tasklists_write"}.isdisjoint(employee)
    assert {"groups_create", "tasklists_write"} <= lead
    assert lead == admin

    # The default employee bundle contains the reviewed read surface. Resource
    # ACLs still decide which concrete Feishu resources are visible at runtime.
    assert {
        "documents_read", "groups_read", "group_members_read", "messages_read",
        "people_search", "calendar_read", "calendar_availability", "tasks_read",
        "meetings_read", "minutes_search", "minutes_read", "base_read",
    } <= employee


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_role_scope_bundles_are_nonempty_and_never_contain_bot_send_scope(role: str) -> None:
    scopes = set(scopes_for_role(role))
    assert "offline_access" in scopes
    assert "im:message.send_as_user" in scopes
    assert "im:message" in scopes
    assert "im:message:send_as_bot" not in scopes


def test_high_value_operation_scope_contracts() -> None:
    expected = {
        ("im", "+messages-send"): {"im:message.send_as_user", "im:message"},
        ("calendar", "+create"): {"calendar:calendar.event:create", "calendar:calendar.event:update"},
        ("calendar", "+update"): {"calendar:calendar.event:update"},
        ("calendar", "+list-attendees"): {"calendar:calendar.event:read"},
        ("calendar", "+meeting"): {"calendar:calendar.event:read"},
        ("calendar", "+rsvp"): {"calendar:calendar.event:reply"},
        ("calendar", "events", "create"): {"calendar:calendar.event:create"},
        ("calendar", "event.attendees", "create"): {"calendar:calendar.event:update"},
        ("vc", "+detail"): {"vc:meeting.meetingevent:read", "vc:record:readonly"},
        ("base", "+base-create"): {
            "base:app:create", "base:table:read", "base:table:create",
            "base:table:update", "base:table:delete",
        },
        ("base", "+table-create"): {
            "base:table:create", "base:field:read", "base:field:create",
            "base:field:update", "base:view:write_only",
        },
        ("base", "+record-batch-create"): {"base:record:create"},
        ("base", "+record-batch-update"): {"base:record:update"},
        ("base", "+record-upsert"): {"base:record:create", "base:record:update"},
        ("task", "+tasklist-create"): {"task:tasklist:write", "task:task:write"},
    }
    for path, scopes in expected.items():
        capabilities = COMMANDS[path]
        assert required_command_scopes(["lark-cli", *path], capabilities) == scopes


def test_registered_mutations_are_marked_writes_and_read_operations_are_not() -> None:
    read_paths = {
        ("docs", "+fetch"), ("docs", "+search"), ("im", "+chat-list"),
        ("im", "+chat-search"), ("im", "chats", "get"),
        ("im", "+chat-members-list"), ("im", "chat.members", "get"),
        ("im", "+chat-messages-list"), ("im", "+messages-search"),
        ("contact", "+search-user"), ("calendar", "+agenda"),
        ("calendar", "+get"), ("calendar", "+search-event"),
        ("calendar", "+list-attendees"), ("calendar", "+meeting"),
        ("calendar", "+freebusy"), ("calendar", "+room-find"),
        ("calendar", "+suggestion"), ("calendar", "events", "list"),
        ("calendar", "events", "get"), ("task", "+get-my-tasks"),
        ("task", "+search"), ("task", "tasks", "get"), ("vc", "+search"),
        ("vc", "+detail"), ("base", "+table-list"), ("base", "+table-get"),
        ("base", "+field-list"), ("base", "+field-get"),
        ("base", "+record-list"), ("base", "+record-search"),
        ("base", "+record-get"), ("minutes", "+search"),
        ("minutes", "+detail"), ("drive", "+task_result"),
    }
    assert read_paths <= set(COMMANDS)
    assert READ_OPERATIONS == read_paths
    for path in read_paths:
        assert not is_write(path), path

    write_paths = set(COMMANDS) - read_paths
    assert write_paths
    for path in write_paths:
        assert is_write(path), path


def test_registered_operations_exist_in_the_installed_cli() -> None:
    """Catch CLI-version drift before an operation reaches the model catalog."""

    executable = shutil.which("lark-cli")
    if executable is None:
        pytest.skip("lark-cli is not installed in this test environment")

    for path in COMMANDS:
        result = subprocess.run(
            [executable, *path, "--help"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        assert result.returncode == 0, (
            operation_key(path), result.stdout[-500:], result.stderr[-500:]
        )


def test_bitable_field_create_exposes_schema_json_without_exposing_output_json() -> None:
    description = asyncio.run(describe_operation("base +field-create"))
    assert "json" in description["input_schema"]["properties"]
    command = build_command(
        "base +field-create",
        {"base-token": "app_test", "table-id": "tbl_test", "json": '{"name":"Status","type":"text"}'},
        description,
    )
    assert "--json" in command and '"name":"Status"' in command
    with pytest.raises(ValueError, match="参数校验失败"):
        build_command(
            "base +field-create",
            {"base-token": "app_test", "table-id": "tbl_test", "json": True},
            description,
        )


def test_native_calendar_create_accepts_user_vchat_payload() -> None:
    """Native create must expose the user-supported vchat body, not only bot owner flags."""

    description = asyncio.run(describe_operation("calendar events create"))
    schema = description["input_schema"]
    assert schema["required"] == ["data", "params"]
    assert "vchat" in schema["properties"]["data"]["properties"]
    vchat = schema["properties"]["data"]["properties"]["vchat"]["properties"]
    assert {"vc_type", "meeting_settings", "meeting_url"} <= set(vchat)

    command = build_command(
        "calendar events create",
        {
            "params": {"calendar_id": "cal_user_primary"},
            "data": {
                "summary": "用户视频会议",
                "start_time": {"datetime": "2030-01-01T07:00:00", "timezone": "Asia/Shanghai"},
                "end_time": {"datetime": "2030-01-01T08:00:00", "timezone": "Asia/Shanghai"},
                "vchat": {"vc_type": "vc"},
            },
        },
        description,
    )
    parts = shlex.split(command)
    assert parts[1:4] == ["calendar", "events", "create"]
    assert json.loads(parts[parts.index("--params") + 1]) == {"calendar_id": "cal_user_primary"}
    assert json.loads(parts[parts.index("--data") + 1])["vchat"] == {"vc_type": "vc"}
    assert parts[-4:] == ["--as", "user", "--format", "json"]
