import pytest

from app.core.calendar_time import calendar_input_schema, normalize_calendar_arguments, parse_local_time
from jsonschema import Draft202012Validator


def test_default_beijing_not_host_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    assert int(parse_local_time("2026-09-09T12:00:00").timestamp()) == 1788926400
    assert int(parse_local_time("2026-09-09T12:00:00+08:00").timestamp()) == 1788926400
    assert parse_local_time("2026-09-09T12:00:00-04:00").utcoffset().total_seconds() == -14400


def test_native_wall_time_converted_without_mutating_approval():
    args = {"data": {"start_time": {"datetime": "2026-09-09T12:00:00"},
                     "end_time": {"datetime": "2026-09-09T12:10:00"}}}
    result = normalize_calendar_arguments("calendar events create", args)
    assert result["data"]["start_time"] == {"timestamp": "1788926400", "timezone": "Asia/Shanghai"}
    assert result["data"]["end_time"]["timestamp"] == "1788927000"
    assert "datetime" in args["data"]["start_time"]


@pytest.mark.parametrize("value,zone", [
    ("2026-09-09T12:00:00Z", "Asia/Shanghai"),
    ("2026-03-08T02:30:00", "America/New_York"),
    ("2026-11-01T01:30:00", "America/New_York"),
    ("2026-09-09", "Asia/Shanghai"),
])
def test_conflicts_or_ambiguous_times_rejected(value, zone):
    with pytest.raises(ValueError):
        parse_local_time(value, zone)


def test_model_cannot_submit_guessed_epoch():
    schema = calendar_input_schema({"properties": {"data": {"properties": {"start_time": {}}}}})
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors({"data": {"start_time": {"timestamp": "1788897600"}}}))
    assert not list(validator.iter_errors({"data": {"start_time": {"datetime": "2026-09-09T12:00:00"}}}))


def test_end_before_start_rejected():
    with pytest.raises(ValueError):
        normalize_calendar_arguments("calendar +create", {"start": "2026-09-09T12:00:00", "end": "2026-09-09T11:00:00"})


def test_model_description_keeps_constraints_without_duplicate_output_schema():
    from app.skills.lark_cli.tool_catalog import model_tool_description
    original = {"operation": "calendar events create", "write": True,
                "input_schema": {"type": "object", "required": ["data"], "description": "x" * 1000},
                "output_schema": {"huge": "unused"}, "help": "redundant"}
    compact = model_tool_description(original)
    assert compact["input_schema"]["required"] == ["data"]
    assert len(compact["input_schema"]["description"]) == 300
    assert "output_schema" not in compact and "help" not in compact
    assert len(original["input_schema"]["description"]) == 1000
