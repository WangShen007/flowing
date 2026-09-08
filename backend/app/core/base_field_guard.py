"""Validate record writes against freshly observed CLI field schemas."""
from __future__ import annotations

import json
import math
import shlex
from typing import Any, Awaitable, Callable

RECORD_WRITES = {"+record-upsert", "+record-batch-create", "+record-batch-update"}


def parse_fields(stdout: str) -> list[dict[str, Any]]:
    payload = json.loads(stdout)
    data = payload.get("data", {})
    fields = data.get("fields")
    if payload.get("ok") is not True or not isinstance(fields, list) or not fields or data.get("has_more"):
        raise ValueError("未取得完整字段定义，未执行写入")
    if data.get("total", len(fields)) != len(fields):
        raise ValueError("字段定义不完整，未执行写入")
    if any(not isinstance(f, dict) or not all(isinstance(f.get(k), str) for k in ("id", "name", "type")) for f in fields):
        raise ValueError("字段定义格式无效")
    return fields


def validate_cells(values: dict[str, Any], fields: list[dict[str, Any]]) -> None:
    if not isinstance(values, dict) or not values:
        raise ValueError("记录字段必须为非空对象")
    for key, value in values.items():
        matches = [f for f in fields if key in (f["id"], f["name"])]
        if len(matches) != 1:
            raise ValueError(f"字段不存在或有歧义：{key}；请重新读取字段映射")
        field = matches[0]
        kind = field["type"]
        if kind not in {"text", "phone", "url", "number", "currency", "percent", "rating", "select", "datetime", "checkbox", "user", "group", "link", "location"}:
            raise ValueError(f"字段 {key} 为只读或尚未审核的类型 {kind}，禁止普通记录写入")
        if field.get("is_readonly") is True or field.get("readonly") is True:
            raise ValueError(f"字段 {key} 为只读")
        if value is None:
            continue  # Platform still decides whether clearing is permitted.
        valid = True
        if kind in {"text", "phone", "url", "datetime"}:
            valid = isinstance(value, str)
        elif kind in {"number", "currency", "percent", "rating"}:
            valid = type(value) in (int, float) and math.isfinite(value)
        elif kind == "checkbox":
            valid = type(value) is bool
        elif kind == "select":
            options = {o["name"] for o in field.get("options", []) if isinstance(o, dict) and isinstance(o.get("name"), str)}
            valid = isinstance(value, list) and all(isinstance(v, str) and v in options for v in value)
            valid = valid and (field.get("multiple") is True or len(value) <= 1)
        elif kind in {"user", "group", "link"}:
            prefix = {"user": "ou_", "group": "oc_", "link": "rec"}[kind]
            valid = isinstance(value, list) and all(isinstance(v, dict) and set(v) == {"id"} and isinstance(v["id"], str) and v["id"].startswith(prefix) for v in value)
            valid = valid and (field.get("multiple") is not False or len(value) <= 1)
        elif kind == "location":
            valid = isinstance(value, dict) and set(value) == {"lng", "lat"} and all(type(v) in (int, float) and math.isfinite(v) for v in value.values())
            valid = valid and -180 <= value["lng"] <= 180 and -90 <= value["lat"] <= 90
        if not valid:
            raise ValueError(f"字段 {key} 的值不符合真实类型或可选项，请读取字段定义后修正")


async def enforce_base_fields(argv: list[str], read: Callable[[str], Awaitable[tuple[bool, str, str]]]) -> None:
    if len(argv) < 3 or argv[1] != "base" or argv[2] not in RECORD_WRITES:
        return
    flags: dict[str, str] = {}
    for i, arg in enumerate(argv):
        if arg.startswith("--"):
            key, sep, value = arg.partition("=")
            if key in {"--base-token", "--table-id", "--json"}:
                if key in flags:
                    raise ValueError("重复的表格写入参数")
                flags[key] = value if sep else (argv[i + 1] if i + 1 < len(argv) else "")
    if not all(flags.get(k) for k in ("--base-token", "--table-id", "--json")):
        raise ValueError("表格写入缺少目标或 JSON")
    payload = json.loads(flags["--json"])
    if not isinstance(payload, dict):
        raise ValueError("记录写入 JSON 必须为对象")
    rows = [payload]
    if argv[2] == "+record-batch-create":
        rows = payload.get("create_records")
    elif argv[2] == "+record-batch-update":
        updates = payload.get("update_records")
        rows = list(updates.values()) if isinstance(updates, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("记录写入列表为空或格式无效")
    query = shlex.join(["lark-cli", "base", "+field-list", "--base-token", flags["--base-token"], "--table-id", flags["--table-id"], "--as", "user", "--format", "json"])
    ok, stdout, stderr = await read(query)
    if not ok:
        raise ValueError("字段校验失败，未执行写入：" + stderr)
    fields = parse_fields(stdout)
    for row in rows:
        validate_cells(row, fields)
