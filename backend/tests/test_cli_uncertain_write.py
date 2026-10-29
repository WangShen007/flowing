import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.skills.lark_cli.skill_runtime import LarkCLISkill


@pytest.mark.parametrize("approved_write,spawned,unknown", [(True, True, True), (False, True, False), (True, False, False)])
def test_timeout_is_unknown_only_after_approved_write_process_started(monkeypatch, approved_write, spawned, unknown):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    monkeypatch.setattr(skill, "_with_user_profile", lambda command, _: command)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda _: {})
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(side_effect=[TimeoutError(), (b"", b"")]))
    create = AsyncMock(return_value=process) if spawned else AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    success, _, error = asyncio.run(skill.execute_command(
        "lark-cli im +messages-send --chat-id oc_test --text test", structured=True,
        approved_write=approved_write,
    ))
    assert not success
    assert error.startswith("execution_result_unknown:") is unknown
    assert create.await_count == 1


@pytest.mark.parametrize("mode", ["direct", "legacy"])
def test_confirmed_write_timeout_is_unknown_and_never_retried(monkeypatch, mode):
    from app.core import account_access, feishu_tokens
    from app.skills.base import SkillContext
    from app.skills.lark_cli.skill_runtime import LarkCLIState

    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.settings = SimpleNamespace(LARK_AGENT_ENGINE="legacy")
    monkeypatch.setattr(account_access, "account_execution_error", lambda _user: "")
    monkeypatch.setattr(feishu_tokens, "enterprise_configured", lambda: False)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda _user: {})
    monkeypatch.setattr(skill, "_with_user_profile", lambda command, _user: command)
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=LarkCLIState(
        installed=True, configured=True, authenticated=True,
    )))
    command = "lark-cli im +messages-send --chat-id oc_test --text test"
    monkeypatch.setattr(skill, "_plan_commands", AsyncMock(return_value=({"commands": []}, [], [])))
    monkeypatch.setattr(skill, "_build_heuristic_step", lambda *_: {"command": command, "expected": "write"})
    monkeypatch.setattr(skill, "_maybe_enrich_slides_command", AsyncMock(return_value=(command, False)))
    repair = AsyncMock(side_effect=AssertionError("uncertain write must not be repaired"))
    monkeypatch.setattr(skill, "_repair_failed_command", repair)
    calls = []
    remote_effects = []

    async def fake_cli(*args, **kwargs):
        calls.append(args)
        # Simulate a remote send succeeding before the local process times out.
        remote_effects.append("synthetic message sent")
        return SimpleNamespace(returncode=0, communicate=AsyncMock(side_effect=[TimeoutError(), (b"", b"")]))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_cli)
    context = SkillContext(user_id="owner", session_id="session", message="发送消息")
    result = asyncio.run(skill.execute(context, query="发送消息", confirm_write=True,
                                      command=command if mode == "direct" else None))
    assert not result.success
    assert len(calls) == 1
    assert remote_effects == ["synthetic message sent"]
    assert result.data["executed_commands"][-1]["status"] == "unknown"
    assert "核实" in result.message
    repair.assert_not_awaited()


def test_resume_does_not_replay_an_unknown_legacy_write(monkeypatch):
    from app.skills.base import SkillContext
    from app.skills.lark_cli.skill_runtime import LarkCLIState

    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.settings = SimpleNamespace(LARK_AGENT_ENGINE="legacy")
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=LarkCLIState(
        installed=True, configured=True, authenticated=True,
    )))
    execute = AsyncMock(side_effect=AssertionError("unknown write cannot be replayed"))
    monkeypatch.setattr(skill, "execute_command", execute)
    record = {"command": "lark-cli im +messages-send --text test", "success": False,
              "status": "unknown", "expected": "write"}
    context = SkillContext(user_id="owner", session_id="session", message="发送", metadata={
        "resume_checkpoint": {"plan": {"summary": "send"}, "executed_commands": [record]},
    })
    result = asyncio.run(skill.execute(context, confirm_write=True))
    assert not result.success and result.data["executed_commands"][-1]["status"] == "unknown"
    execute.assert_not_awaited()


def test_read_repair_write_requires_approval_and_timeout_is_not_retried(monkeypatch):
    from app.core import account_access, feishu_tokens
    from app.skills.base import SkillContext
    from app.skills.lark_cli.skill_runtime import LarkCLIState

    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.settings = SimpleNamespace(LARK_AGENT_ENGINE="legacy")
    monkeypatch.setattr(account_access, "account_execution_error", lambda _user: "")
    monkeypatch.setattr(feishu_tokens, "enterprise_configured", lambda: False)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda _user: {})
    monkeypatch.setattr(skill, "_with_user_profile", lambda command, _user: command)
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=LarkCLIState(
        installed=True, configured=True, authenticated=True,
    )))
    read = "lark-cli im +chat-list --as user"
    write = "lark-cli im +messages-send --chat-id oc_test --text test"
    monkeypatch.setattr(skill, "_plan_commands", AsyncMock(return_value=({"commands": []}, [], [])))
    monkeypatch.setattr(skill, "_select_relevant_skills", lambda _query: [])
    monkeypatch.setattr(skill, "_select_references", lambda _query, _skills: [])
    monkeypatch.setattr(skill, "_build_heuristic_step", lambda *_: {"command": read, "expected": "read"})
    monkeypatch.setattr(skill, "_maybe_enrich_slides_command", AsyncMock(side_effect=lambda _q, c: (c, False)))
    repair = AsyncMock(return_value=write)
    monkeypatch.setattr(skill, "_repair_failed_command", repair)
    calls = []

    async def fake_cli(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", b"invalid argument")))
        return SimpleNamespace(returncode=0, communicate=AsyncMock(side_effect=[TimeoutError(), (b"", b"")]))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_cli)
    context = SkillContext(user_id="owner", session_id="session", message="查询")
    pending = asyncio.run(skill.execute(context, query="查询", confirm_write=False))
    assert pending.data["approval_required"]
    assert pending.data["pending_step"]["command"] == write
    assert pending.data["plan"]["commands"][-1]["expected"] == "write"
    assert len(calls) == 1
    context.metadata = {"resume_checkpoint": {
        "plan": pending.data["plan"], "pending_step": pending.data["pending_step"],
        "executed_commands": pending.data["executed_commands"],
    }}
    result = asyncio.run(skill.execute(context, query="查询", confirm_write=True))
    assert not result.success and result.data["executed_commands"][-1]["status"] == "unknown"
    assert len(calls) == 2
    assert repair.await_count == 1


def test_uncertain_write_logging_does_not_expose_provider_error(monkeypatch, caplog):
    skill = LarkCLISkill.__new__(LarkCLISkill)
    monkeypatch.setattr(skill, "_with_user_profile", lambda command, _user: command)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda _user: {})
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(side_effect=RuntimeError("synthetic-secret")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with caplog.at_level("WARNING", logger="app.skills.lark_cli.skill_runtime"):
        success, _, error = asyncio.run(skill.execute_command(
            "lark-cli im +messages-send --chat-id oc_test --text test", structured=True, approved_write=True,
        ))
    assert not success and error.startswith("execution_result_unknown:")
    assert "CLI execution failed" in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_real_async_timeout_reaps_owned_write_process(monkeypatch):
    import sys

    skill = LarkCLISkill.__new__(LarkCLISkill)
    monkeypatch.setattr(skill, "_with_user_profile", lambda command, _user: command)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda _user: {})
    monkeypatch.setattr(skill, "_split_lark_cli_args", lambda _command: [
        sys.executable, "-u", "-c", "import time; time.sleep(30)",
    ])
    real_spawn = asyncio.create_subprocess_exec
    processes = []

    async def capture_spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_spawn)
    success, _, error = asyncio.run(skill.execute_command(
        "lark-cli im +messages-send --chat-id oc_test --text test", timeout=0.1,
        structured=True, approved_write=True,
    ))
    assert not success and error.startswith("execution_result_unknown:")
    assert len(processes) == 1 and processes[0].returncode is not None


@pytest.mark.parametrize(
    "repairs, expected_count, expected_reason, preflight_denied",
    [
        (["lark-cli im +messages-send --chat-id oc_test --text test"], 1, "写操作失败，已停止", False),
        ([""], 0, "未能生成修复命令", False),
        (["lark-cli im +chat-list --as user"], 0, "生成了重复命令", False),
        ([f"lark-cli im +chat-list --as user --page-size {value}" for value in (10, 20, 30)], 3, "已达到自动修复次数上限", False),
        (["lark-cli im +chat-list --as user --page-size 10", ""], 1, "未能生成修复命令", True),
    ],
)
def test_stream_reports_repair_attempts_and_stop_reasons(monkeypatch, repairs, expected_count, expected_reason, preflight_denied):
    from app.core import account_access, feishu_tokens
    from app.skills.base import SkillContext
    from app.skills.lark_cli.skill_runtime import LarkCLIState

    skill = LarkCLISkill.__new__(LarkCLISkill)
    skill.settings = SimpleNamespace(LARK_AGENT_ENGINE="legacy")
    skill.client = None
    account_check = Mock(side_effect=["", "本地账号不可执行"] if preflight_denied else None, return_value="")
    monkeypatch.setattr(account_access, "account_execution_error", account_check)
    monkeypatch.setattr(feishu_tokens, "enterprise_configured", lambda: False)
    monkeypatch.setattr(skill, "_cli_env_for_user", lambda _user: {})
    monkeypatch.setattr(skill, "_with_user_profile", lambda command, _user: command)
    monkeypatch.setattr(skill, "_probe_cli_state", AsyncMock(return_value=LarkCLIState(
        installed=True, configured=True, authenticated=True,
    )))
    monkeypatch.setattr(skill, "_plan_commands", AsyncMock(return_value=({"commands": []}, [], [])))
    read = "lark-cli im +chat-list --as user"
    monkeypatch.setattr(skill, "_build_heuristic_step", lambda *_: {"command": read, "expected": "read"})
    monkeypatch.setattr(skill, "_maybe_enrich_slides_command", AsyncMock(side_effect=lambda _q, c: (c, False)))
    repair = AsyncMock(side_effect=repairs)
    monkeypatch.setattr(skill, "_repair_failed_command", repair)
    started = []

    async def fake_cli(*args, **_kwargs):
        started.append(args)
        return SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", b"synthetic failure")))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_cli)
    context = SkillContext(user_id="owner", session_id="session", message="查询")

    async def run():
        return [event async for event in skill.execute_stream(context, query="查询", confirm_write=True)]

    events = asyncio.run(run())
    notices = [event["content"] for event in events if event.get("type") == "progress"
               and event.get("content", "").startswith("命令执行失败")]
    assert len(notices) == 1
    # A preflight denial is an attempted command, but never starts a process.
    assert len(started) == expected_count + 1 - int(preflight_denied)
    assert f"已尝试执行修复命令 {expected_count} 次" in notices[0]
    assert expected_reason in notices[0]
    assert repair.await_count == (2 if preflight_denied else 3 if expected_count == 3 else 1)
    if expected_reason.startswith("写操作"):
        records = [event["data"]["executed_commands"] for event in events
                   if event.get("type") == "metadata" and "executed_commands" in event.get("data", {})][-1]
        assert records[-1]["expected"] == "write"
