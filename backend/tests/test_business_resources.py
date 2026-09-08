import asyncio
import json
import shlex
from unittest.mock import AsyncMock

import pytest

from app.core.base_field_guard import enforce_base_fields, parse_fields, validate_cells
from app.core.business_resources import delete_resource, list_resources, save_resource
from app.core.storage import SQLiteStore


FIELDS = [
    {"id": "fldStatus", "name": "状态", "type": "select", "multiple": False,
     "options": [{"name": "待办"}, {"name": "已完成"}]},
    {"id": "fldName", "name": "名称", "type": "text"},
    {"id": "fldCalc", "name": "统计", "type": "formula"},
    {"id": "fldDone", "name": "完成", "type": "checkbox"},
]


def test_resources_isolated_persistent_and_delete_is_local(tmp_path):
    db = SQLiteStore(tmp_path / "resources.db")
    for account in ("a.b", "a-b"):
        db.execute("INSERT INTO accounts VALUES (?,?,?,1,1)", (account, account, ""))
    config = {"alias": "项目表", "base_token": "BaseToken12345", "table_id": "tblProjects", "fields": {"完成状态": "fldStatus"}}
    save_resource(db, "a.b", config)
    assert list_resources(db, "a-b") == []
    assert list_resources(SQLiteStore(db.db_path), "a.b")[0]["fields"] == config["fields"]
    delete_resource(db, "a-b", "项目表")
    assert len(list_resources(db, "a.b")) == 1
    save_resource(db, "a.b", {**config, "fields": {"名称": "fldName"}})
    assert len(list_resources(db, "a.b")) == 1
    delete_resource(db, "a.b", "项目表")
    assert list_resources(db, "a.b") == []


@pytest.mark.parametrize("values", [
    {"不存在": "x"}, {"统计": None}, {"状态": "已完成"}, {"状态": ["搞定"]},
    {"状态": ["已完成", "待办"]}, {"完成": "true"}, {"名称": 1}, {},
])
def test_reject_invalid_or_computed_fields(values):
    with pytest.raises(ValueError):
        validate_cells(values, FIELDS)


def test_valid_values_and_stable_ids():
    validate_cells({"fldStatus": ["已完成"], "名称": "验收", "完成": True}, FIELDS)
    renamed = [{**f, "name": "改名"} if f["id"] == "fldStatus" else f for f in FIELDS]
    validate_cells({"fldStatus": ["已完成"]}, renamed)
    with pytest.raises(ValueError):
        validate_cells({"状态": ["已完成"]}, renamed)


def test_incomplete_schema_is_not_trusted():
    for data in ({"fields": FIELDS, "has_more": True}, {"fields": FIELDS, "total": 9}, {"fields": []}):
        with pytest.raises(ValueError):
            parse_fields(json.dumps({"ok": True, "data": data}))


@pytest.mark.parametrize("operation,payload", [
    ("+record-upsert", {"状态": ["已完成"]}),
    ("+record-batch-create", {"create_records": [{"状态": ["已完成"]}]}),
    ("+record-batch-update", {"update_records": {"rec123": {"状态": ["已完成"]}}}),
])
def test_guard_reads_fresh_schema_for_each_write(operation, payload):
    read = AsyncMock(return_value=(True, json.dumps({"ok": True, "data": {"fields": FIELDS}}), ""))
    argv = ["lark-cli", "base", operation, "--base-token", "BaseToken12345", "--table-id", "tblProjects", "--json", json.dumps(payload)]
    asyncio.run(enforce_base_fields(argv, read))
    assert shlex.split(read.call_args.args[0])[1:3] == ["base", "+field-list"]
    read.return_value = (False, "", "permission denied")
    with pytest.raises(ValueError, match="未执行写入"):
        asyncio.run(enforce_base_fields(argv, read))
    assert read.await_count == 2


def test_read_operations_do_not_trigger_recursive_validation():
    read = AsyncMock()
    asyncio.run(enforce_base_fields(["lark-cli", "base", "+field-list"], read))
    read.assert_not_awaited()


def test_runtime_stops_before_write_process_on_field_guard_failure(monkeypatch):
    from app.core import base_field_guard, feishu_tokens
    from app.skills.lark_cli.skill import LarkCLISkill

    skill = LarkCLISkill()
    monkeypatch.setattr(skill, "_with_user_profile", lambda command, user: command)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda user: {})
    monkeypatch.setattr(feishu_tokens, "enterprise_configured", lambda: False)
    guard = AsyncMock(side_effect=ValueError("字段已变为只读"))
    monkeypatch.setattr(base_field_guard, "enforce_base_fields", guard)
    spawn = AsyncMock(side_effect=AssertionError("must not execute a rejected write"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = asyncio.run(skill.execute_command(
        "lark-cli base +record-upsert --base-token BaseToken12345 --table-id tblProjects --json '{}'",
        structured=True, approved_write=True))
    assert not result[0] and "字段已变为只读" in result[2]
    assert not result[2].startswith("execution_result_unknown:")
    guard.assert_awaited_once()
    spawn.assert_not_awaited()
