import asyncio
import json

from app.skills.lark_cli.skill import LarkCLISkill


def test_summary_keeps_evidence_and_user_request_for_model() -> None:
    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.client = object()
    captured = {}

    async def summarize(**kwargs):
        captured.update(kwargs)
        return "文档内容：测试正文"

    skill._run_llm_text = summarize
    results = [{"success": True, "stdout": "测试正文", "command": "lark-cli docs +fetch --doc test"}]
    result = asyncio.run(skill._summarize_execution("读取全文", {"summary": "执行计划"}, results))
    assert result == "文档内容：测试正文"
    payload = json.loads(captured["user_prompt"])
    assert payload["query"] == "读取全文"
    assert payload["results"] == results


def test_unavailable_summary_does_not_dump_logs_or_claim_partial_success() -> None:
    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.client = None
    results = [
        {"success": True, "command": "lark-cli docs +create", "stdout": "raw-json"},
        {"success": False, "command": "lark-cli im +messages-send", "stderr": "raw-error"},
    ]
    result = asyncio.run(skill._summarize_execution("创建并通知", {}, results))
    assert "部分操作未成功" in result
    assert all(value not in result for value in ("lark-cli", "raw-json", "raw-error"))
    assert "未成功" in skill._fallback_summary({}, results[1:])
    assert "没有执行" in skill._fallback_summary({}, [])
