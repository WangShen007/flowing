"""Malformed external input remains a ValueError at public validation boundaries."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.core import task_access
from app.core.base_field_guard import enforce_base_fields
from app.skills.lark_cli.tool_catalog import build_command
from data.manage_users import load_accounts, load_users


def test_invalid_base_json_keeps_value_error_contract():
    argv = ["lark-cli", "base", "+record-upsert", "--base-token", "app", "--table-id", "tbl", "--json", "null"]
    with pytest.raises(ValueError, match="必须为对象"):
        asyncio.run(enforce_base_fields(argv, AsyncMock()))


def test_invalid_task_id_keeps_value_error_contract():
    with pytest.raises(ValueError, match="必须是字符串"):
        task_access.target_ids(["--params", json.dumps({"task_guid": 1})], ("task", "tasks", "delete"))


@pytest.mark.parametrize("msg_type,content", [
    ("text", 12),
    ("text", json.dumps({"text": 12})),
    ("post", json.dumps([])),
    ("post", json.dumps({"zh_cn": {"content": 12}})),
])
def test_invalid_message_content_keeps_value_error_contract(msg_type, content):
    description = {"operation": "im +messages-send", "input_schema": {"type": "object"}}
    with pytest.raises(ValueError):
        build_command("im +messages-send", {"msg-type": msg_type, "content": content}, description)


@pytest.mark.parametrize("function,payload", [(load_users, {}), (load_users, [1]), (load_accounts, {})])
def test_invalid_admin_import_keeps_value_error_contract(tmp_path, function, payload):
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        function(path)
