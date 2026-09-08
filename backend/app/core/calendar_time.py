"""Calendar wall-clock inputs are converted by code, not model arithmetic."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Shanghai"


def parse_local_time(value: str, timezone: str | None = None) -> datetime:
    try:
        zone = ZoneInfo(timezone or DEFAULT_TIMEZONE)
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("请提供有效日期时间和 IANA 时区，默认 Asia/Shanghai") from exc
    if "T" not in value and " " not in value:
        raise ValueError("具体时间须包含日期和时分；全天日程使用 date")
    if parsed.tzinfo is not None:
        if timezone is None:
            return parsed
        if parsed.utcoffset() != parsed.astimezone(zone).utcoffset():
            raise ValueError("日期时间偏移与指定时区不一致，请更正")
        return parsed.astimezone(zone)
    aware = parsed.replace(tzinfo=zone)
    if datetime.fromtimestamp(aware.timestamp(), zone).replace(tzinfo=None) != parsed:
        raise ValueError("此当地时间因夏令时跳转不存在")
    if aware.utcoffset() != parsed.replace(tzinfo=zone, fold=1).utcoffset():
        raise ValueError("此当地时间存在夏令时歧义，请提供明确偏移")
    return aware


def calendar_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(schema)
    properties = schema.get("properties", {}).get("data", {}).get("properties", {})
    for key in ("start_time", "end_time"):
        if key not in properties:
            continue
        properties[key] = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "datetime": {"type": "string", "description": "当地日期时间，如 2026-09-09T12:00:00；禁止心算时间戳"},
                "date": {"type": "string", "description": "全天日程日期 YYYY-MM-DD；结束日期不包含当天"},
                "timezone": {"type": "string", "default": DEFAULT_TIMEZONE},
            },
            "oneOf": [{"required": ["datetime"], "not": {"required": ["date"]}},
                      {"required": ["date"], "not": {"required": ["datetime"]}}],
        }
    return schema


def normalize_calendar_arguments(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    arguments = deepcopy(arguments)
    if operation == "calendar events create":
        values = arguments.get("data", {})
        points = []
        modes = []
        for key in ("start_time", "end_time"):
            value = values[key]
            zone = value.get("timezone") or DEFAULT_TIMEZONE
            if "datetime" in value:
                parsed = parse_local_time(value["datetime"], zone)
                values[key] = {"timestamp": str(int(parsed.timestamp())), "timezone": zone}
                modes.append("time")
            else:
                parsed_date = datetime.strptime(value["date"], "%Y-%m-%d").date()
                parsed = datetime.combine(parsed_date, time(), ZoneInfo(zone))
                values[key] = {"date": parsed_date.isoformat(), "timezone": zone}
                modes.append("date")
            points.append(parsed.timestamp())
        if modes[0] != modes[1] or points[1] <= points[0]:
            raise ValueError("日程起止类型必须一致，结束时间必须晚于开始时间")
    elif operation in {"calendar +create", "calendar +update"}:
        points = []
        for key in ("start", "end"):
            if key in arguments:
                arguments[key] = parse_local_time(arguments[key]).isoformat()
                points.append(datetime.fromisoformat(arguments[key]).timestamp())
        if len(points) == 2 and points[1] <= points[0]:
            raise ValueError("结束时间必须晚于开始时间")
    return arguments
