"""Local subprocess tests for setup output and cancellation; no CLI accounts."""
import asyncio
import subprocess
import sys

from app.api.routes import lark_setup


def test_setup_stream_forwards_stdout_stderr_and_exit_status():
    async def run():
        return [event async for event in lark_setup._stream_popen_events([
            sys.executable, "-u", "-c",
            "import sys; print('setup output', flush=True); print('setup warning', file=sys.stderr, flush=True)",
        ])]

    events = asyncio.run(run())
    assert any(item.get("stream") == "stdout" and "setup output" in item["chunk"] for item in events)
    assert any(item.get("stream") == "stderr" and "setup warning" in item["chunk"] for item in events)
    assert events[-1] == {"type": "process_done", "return_code": 0}


def test_setup_stream_closing_terminates_only_its_child(monkeypatch):
    real_spawn = subprocess.Popen
    spawned = []

    def capture_spawn(*args, **kwargs):
        child = real_spawn(*args, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(lark_setup.subprocess, "Popen", capture_spawn)

    async def run():
        events = lark_setup._stream_popen_events([
            sys.executable, "-u", "-c", "import time; print('ready', flush=True); time.sleep(30)",
        ])
        first = await anext(events)
        assert first["type"] == "terminal" and "ready" in first["chunk"]
        await events.aclose()

    asyncio.run(run())
    assert len(spawned) == 1
    assert spawned[0].poll() is not None


def test_cancellation_during_spawn_awaits_and_reaps_child(monkeypatch):
    from threading import Event

    real_spawn = subprocess.Popen
    entered, release = Event(), Event()
    spawned = []

    def delayed_spawn(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        child = real_spawn(*args, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(lark_setup.subprocess, "Popen", delayed_spawn)

    async def run():
        events = lark_setup._stream_popen_events([sys.executable, "-c", "import time; time.sleep(30)"])
        task = asyncio.create_task(anext(events))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancellation was not propagated")
        await events.aclose()

    asyncio.run(run())
    assert len(spawned) == 1
    assert spawned[0].poll() is not None
