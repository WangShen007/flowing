import asyncio
import shlex
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from app.skills.base import SkillContext
from app.core.scenario_templates import render_template
from app.core.scheduled_tasks import parse_schedule_intent
from app.skills.lark_cli.skill import LarkCLISkill


def test_rendered_document_preserves_title_and_multiline_body() -> None:
    skill = LarkCLISkill.__new__(LarkCLISkill)
    query = render_template("create_doc", {"title": "模板测试0907", "content": "第一行\n第二行"})
    plan = skill._build_doc_create_plan(query)
    assert plan is not None
    args = shlex.split(plan["commands"][0]["command"])
    assert args[args.index("--title") + 1] == "模板测试0907"
    assert args[args.index("--content") + 1] == "第一行\n第二行"
    assert not skill._is_write_request(render_template("meeting_summary", {"keyword": "测试"}))
    assert skill._is_write_request(query)
    assert skill._build_doc_create_plan(query + "\n\n内容生成开关：已启用 AI 扩写") is None


def test_scheduled_markdown_template_keeps_paragraphs_and_code_indent() -> None:
    skill = LarkCLISkill.__new__(LarkCLISkill)
    body = "# 标题\n\n```python\nif True:\n    print(1)\n```"
    query = render_template("markdown_to_doc", {"title": "Markdown定时测试", "markdown_content": body})
    intent = parse_schedule_intent(
        "定时今天 23:59 执行：" + query,
        now=datetime(2026, 9, 7, 20, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert intent is not None
    plan = skill._build_doc_create_plan(intent.task_message)
    assert plan is not None
    args = shlex.split(plan["commands"][0]["command"])
    assert args[args.index("--title") + 1] == "Markdown定时测试"
    assert args[args.index("--content") + 1] == body
    repaired = skill._repair_command(plan["commands"][0]["command"])
    assert not skill._has_unquoted_shell_control_operator(repaired)
    args = skill._split_lark_cli_args(repaired)
    assert args is not None
    assert args[args.index("--content") + 1] == body
    assert skill._has_unquoted_shell_control_operator(repaired + "\nlark-cli docs +fetch --doc other")


def test_base_import_template_title() -> None:
    query = render_template("base_import", {"name": "导入测试", "file_path": "/tmp/test.csv"})
    parsed = LarkCLISkill._parse_bitable_import_request(query)
    assert parsed == {"title": "导入测试", "file_path": "/tmp/test.csv"}


def test_explicit_title_and_supported_content_flags() -> None:
    skill = LarkCLISkill.__new__(LarkCLISkill)
    plan = skill._build_doc_create_plan(
        "创建一份私人飞书文档，标题为 Feishu CLI Web 测试 20260907，正文：模型连接成功。"
    )
    assert plan is not None
    args = shlex.split(plan["commands"][0]["command"])
    assert args[args.index("--title") + 1] == "Feishu CLI Web 测试 20260907"
    assert args[args.index("--content") + 1] == "模型连接成功。"
    assert args[args.index("--doc-format") + 1] == "markdown"
    assert args[args.index("--as") + 1] == "user"
    assert "--markdown" not in args


def test_command_repair_preserves_content_and_xml_format() -> None:
    skill = LarkCLISkill.__new__(LarkCLISkill)
    xml = 'lark-cli docs +create --content "<p>literal --markdown value</p>" --doc-format xml'
    assert skill._repair_command(xml) == xml
    legacy = 'lark-cli docs +create --title "Test" --markdown "Keep --content literal"'
    args = shlex.split(skill._repair_command(legacy))
    assert args[args.index("--content") + 1] == "Keep --content literal"
    assert args[args.index("--doc-format") + 1] == "markdown"


def test_read_only_constraint_does_not_mask_actual_write() -> None:
    skill = LarkCLISkill.__new__(LarkCLISkill)
    query = "读取文档内容，只读取，不修改。"
    assert not skill._is_write_request(query, ["lark-cli docs +fetch --doc test"])
    assert skill._is_write_request(query, ["lark-cli docs +create --title test"])
    assert skill._is_write_request("不修改，但创建新的文档", ["lark-cli docs +fetch --doc test"])


def test_registry_classifies_new_writes_and_unregistered_commands_fail_closed() -> None:
    skill = LarkCLISkill.__new__(LarkCLISkill)
    for command in (
        "lark-cli calendar +rsvp --event-id ev_test",
        "lark-cli base +table-update --base-token app_test --table-id tbl_test",
        "lark-cli base +field-create --base-token app_test --table-id tbl_test",
    ):
        assert skill._is_write_request("查看结果", [command])
    assert not skill._is_write_request("查看结果", ["lark-cli calendar +meeting --event-id ev_test"])
    assert skill._is_write_request("查看结果", ["lark-cli calendar +future-operation"])


def test_direct_registered_write_command_requires_confirmation(monkeypatch) -> None:
    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.settings = SimpleNamespace(LARK_AGENT_ENGINE="legacy")
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=SimpleNamespace(
        installed=True, configured=True, authenticated=True,
    )))
    execute = AsyncMock(return_value=(True, '{"ok":true}', ""))
    monkeypatch.setattr(skill, "execute_command", execute)
    monkeypatch.setattr(skill, "_summarize_execution", AsyncMock(return_value="已完成"))
    context = SkillContext(user_id="owner", session_id="session", message="回复日程")
    command = "lark-cli calendar +rsvp --event-id ev_test"

    pending = asyncio.run(skill.execute(context, query="查看结果", command=command))
    assert not pending.success
    assert pending.data["approval_required"]
    assert execute.await_count == 0

    completed = asyncio.run(skill.execute(context, query="查看结果", command=command, confirm_write=True))
    assert completed.success
    assert execute.await_count == 1
