import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.api.routes import chat
from app.api.routes.auth import AccountInfo


@pytest.fixture
def isolated_admission(monkeypatch):
    monkeypatch.setattr(chat, "_active_sessions", set())
    monkeypatch.setattr(chat, "_active_runs", {})
    monkeypatch.setattr(chat, "get_settings", lambda: SimpleNamespace(CHAT_MAX_ACTIVE_PER_PROCESS=3, CHAT_MAX_ACTIVE_PER_ACCOUNT=2))
    return AccountInfo(account="owner", name="Owner")


def test_nonstream_duplicate_is_rejected_and_reservation_released(isolated_admission, monkeypatch):
    async def exercise():
        started, finish = asyncio.Event(), asyncio.Event()
        async def execute(request, account):
            started.set()
            await finish.wait()
            return {"ok": True}
        monkeypatch.setattr(chat, "_chat_impl", execute)
        first = asyncio.create_task(chat.chat(chat.ChatRequest(message="work", session_id="one", stream=False), isolated_admission))
        await started.wait()
        with pytest.raises(HTTPException) as error:
            await chat.chat(chat.ChatRequest(message="duplicate", session_id="one", stream=False), isolated_admission)
        assert error.value.status_code == 409
        finish.set()
        await first
        assert not chat._active_sessions and not chat._active_runs
    asyncio.run(exercise())


def test_account_limit_rejects_before_implementation(isolated_admission, monkeypatch):
    chat._active_sessions.update({("owner", "one"), ("owner", "two")})
    async def forbidden(*args):
        pytest.fail("overloaded request must not execute")
    monkeypatch.setattr(chat, "_chat_impl", forbidden)
    with pytest.raises(HTTPException) as error:
        asyncio.run(chat.chat(chat.ChatRequest(message="work", session_id="three"), isolated_admission))
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "3"
    assert len(chat._active_sessions) == 2


def test_stream_failure_releases_reservation(isolated_admission, monkeypatch):
    async def execute(*args):
        async def chunks():
            yield "first"
            raise RuntimeError("stream failed")
        return StreamingResponse(chunks())
    monkeypatch.setattr(chat, "_chat_impl", execute)
    async def exercise():
        response = await chat.chat(chat.ChatRequest(message="work", session_id="one"), isolated_admission)
        assert chat._active_sessions
        with pytest.raises(RuntimeError):
            async for _ in response.body_iterator:
                pass
        assert not chat._active_sessions and not chat._active_runs
    asyncio.run(exercise())


def test_other_account_has_capacity_until_worker_limit(isolated_admission, monkeypatch):
    chat._active_sessions.update({("someone", "one"), ("someone", "two")})
    async def execute(*args):
        return {"ok": True}
    monkeypatch.setattr(chat, "_chat_impl", execute)
    assert asyncio.run(chat.chat(chat.ChatRequest(message="work"), isolated_admission)) == {"ok": True}
    chat._active_sessions.add(("third-account", "three"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(chat.chat(chat.ChatRequest(message="work"), isolated_admission))
    assert error.value.status_code == 429


def test_nonstream_exception_releases_reservation(isolated_admission, monkeypatch):
    async def execute(*args):
        raise RuntimeError("provider error")
    monkeypatch.setattr(chat, "_chat_impl", execute)
    with pytest.raises(RuntimeError):
        asyncio.run(chat.chat(chat.ChatRequest(message="work", stream=False), isolated_admission))
    assert not chat._active_sessions and not chat._active_runs
