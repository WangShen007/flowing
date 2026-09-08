import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
