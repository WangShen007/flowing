from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path
from queue import Empty
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import scheduled_tasks as scheduler
from app.core.storage import SQLiteStore


def _insert_due_task(database: SQLiteStore, *, schedule_type: str = "once") -> int:
    now = database.now()
    return database.execute_insert(
        """
        INSERT INTO scheduled_tasks(
            user_id, session_id, original_request, task_message, schedule_type,
            time_of_day, timezone, next_run_at, status, created_at, updated_at
        ) VALUES ('owner', 'session', 'request', 'query', ?, '09:00',
                  'Asia/Shanghai', ?, 'active', ?, ?)
        """,
        (schedule_type, now - 1, now, now),
    )


@pytest.fixture
def isolated_scheduler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = SQLiteStore(tmp_path / "scheduled.sqlite3")
    monkeypatch.setattr(scheduler, "store", database)
    yield database, scheduler.ScheduledTaskStore()


def _claim_in_fork(queue: multiprocessing.Queue, now: int, barrier=None) -> None:
    # The child inherits the database path but has its own process-local lock.
    # SQLite's BEGIN IMMEDIATE is therefore what arbitrates the claim.
    from app.core import scheduled_tasks as child_scheduler

    if barrier is not None:
        barrier.wait()
    claimed = child_scheduler.ScheduledTaskStore().claim_due(1, now_ts=now)
    queue.put(bool(claimed))


def _pause_in_fork(queue: multiprocessing.Queue, db_path: str, barrier) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import scheduled_tasks as scheduled_route
    from app.api.routes.auth import AccountInfo, get_current_account
    from app.core.storage import SQLiteStore

    scheduled_route.store = SQLiteStore(Path(db_path))
    app = FastAPI()
    app.include_router(scheduled_route.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="owner", name="Owner")
    barrier.wait()
    with TestClient(app) as client:
        response = client.post("/scheduled-tasks/1/pause")
    queue.put(response.status_code)


def test_claim_is_atomic_across_two_processes(isolated_scheduler) -> None:
    database, _ = isolated_scheduler
    task_id = _insert_due_task(database)
    assert task_id == 1
    now = database.now()
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    workers = [context.Process(target=_claim_in_fork, args=(queue, now)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    results = []
    for _ in workers:
        try:
            results.append(queue.get(timeout=2))
        except Empty:
            pytest.fail("scheduler child did not report its claim result")
    assert sorted(results) == [False, True]
    assert database.query_one("SELECT status FROM scheduled_tasks WHERE id = 1")["status"] == "running"
    assert database.query_one("SELECT COUNT(*) AS count FROM scheduled_task_runs WHERE task_id = 1")["count"] == 1


def test_acknowledged_unknown_does_not_require_confirmation_twice(isolated_scheduler, monkeypatch):
    from fastapi import HTTPException
    from app.api.routes import scheduled_tasks as route
    from app.api.routes.auth import AccountInfo

    database, tasks = isolated_scheduler
    monkeypatch.setattr(route, "store", database)
    task_id = _insert_due_task(database)
    claimed = tasks.claim_due(task_id, now_ts=database.now())
    assert claimed is not None
    tasks.mark_unknown(claimed, "network interrupted")
    account = AccountInfo(account="owner", name="Owner")

    async def exercise():
        with pytest.raises(HTTPException) as denied:
            await route.resume_scheduled_task(task_id, None, account)
        assert denied.value.status_code == 409
        await route.resume_scheduled_task(task_id, route.ScheduledTaskResumeRequest(confirm_unknown=True), account)
        result = database.loads(database.query_one("SELECT last_result_json FROM scheduled_tasks WHERE id = ?", (task_id,))["last_result_json"], {})
        assert result["status"] == "recovery_acknowledged"
        assert result["requires_confirmation"] is False
        assert result["previous_status"] == "unknown"
        await route.pause_scheduled_task(task_id, account)
        await route.resume_scheduled_task(task_id, None, account)

    asyncio.run(exercise())


def test_pause_and_claim_race_has_one_serialized_winner(isolated_scheduler) -> None:
    database, _ = isolated_scheduler
    _insert_due_task(database)
    now = database.now()
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    barrier = context.Barrier(2)
    claimer = context.Process(target=_claim_in_fork, args=(queue, now, barrier))
    pauser = context.Process(target=_pause_in_fork, args=(queue, str(database.db_path), barrier))
    claimer.start()
    pauser.start()
    for worker in (claimer, pauser):
        worker.join(timeout=10)
        assert worker.exitcode == 0

    values = [queue.get(timeout=2), queue.get(timeout=2)]
    claim_won = True in values
    pause_status = next(value for value in values if type(value) is int)
    assert (claim_won, pause_status) in {(True, 409), (False, 200)}
    task_status = database.query_one("SELECT status FROM scheduled_tasks WHERE id = 1")["status"]
    assert task_status in {"running", "paused"}


def test_restart_marks_inflight_occurrence_unknown_and_pauses_task(isolated_scheduler) -> None:
    database, tasks = isolated_scheduler
    task_id = _insert_due_task(database)
    claimed = tasks.claim_due(task_id, now_ts=database.now())
    assert claimed is not None

    assert tasks.recover_running() == 1
    task = database.query_one("SELECT status, last_result_json FROM scheduled_tasks WHERE id = ?", (task_id,))
    run = database.query_one("SELECT status, result_json FROM scheduled_task_runs WHERE task_id = ?", (task_id,))
    assert task["status"] == "paused"
    assert database.loads(task["last_result_json"], {})["status"] == "unknown"
    assert run["status"] == "unknown"
    assert database.loads(run["result_json"], {})["requires_confirmation"] is True
    assert tasks.claim_due(task_id, now_ts=database.now()) is None


def test_explicit_resume_creates_a_new_occurrence_after_unknown(isolated_scheduler) -> None:
    database, tasks = isolated_scheduler
    task_id = _insert_due_task(database)
    claimed = tasks.claim_due(task_id, now_ts=database.now())
    assert claimed is not None
    tasks.mark_unknown(claimed, "provider connection closed after write")

    database.execute("UPDATE scheduled_tasks SET status = 'active', updated_at = ? WHERE id = ?", (database.now(), task_id))
    retried = tasks.claim_due(task_id, now_ts=database.now())
    assert retried is not None
    assert retried["scheduled_task_run_id"] != claimed["scheduled_task_run_id"]
    assert retried["scheduled_task_occurrence_key"] != claimed["scheduled_task_occurrence_key"]
    assert database.query_one("SELECT COUNT(*) AS count FROM scheduled_task_runs WHERE task_id = ?", (task_id,))["count"] == 2


def test_completion_updates_task_and_occurrence_together(isolated_scheduler) -> None:
    database, tasks = isolated_scheduler
    task_id = _insert_due_task(database)
    claimed = tasks.claim_due(task_id, now_ts=database.now())
    assert claimed is not None
    assert tasks.complete_run(claimed, {"success": True, "message": "done"}) is True

    task = database.query_one("SELECT status, run_count FROM scheduled_tasks WHERE id = ?", (task_id,))
    run = database.query_one("SELECT status, result_json FROM scheduled_task_runs WHERE task_id = ?", (task_id,))
    assert (task["status"], task["run_count"]) == ("completed", 1)
    assert run["status"] == "succeeded"
    assert database.loads(run["result_json"], {})["message"] == "done"
    assert tasks.complete_run(claimed, {"success": True}) is False


@pytest.mark.parametrize("schedule_type", ["daily", "weekdays"])
def test_recurring_success_stays_active_and_schedules_next_occurrence(isolated_scheduler, schedule_type: str) -> None:
    database, tasks = isolated_scheduler
    task_id = _insert_due_task(database, schedule_type=schedule_type)
    claimed = tasks.claim_due(task_id, now_ts=database.now())
    assert claimed is not None

    before = database.now()
    assert tasks.complete_run(claimed, {"success": True, "message": "done"}) is True
    task = database.query_one("SELECT status, next_run_at, run_count FROM scheduled_tasks WHERE id = ?", (task_id,))
    assert task["status"] == "active"
    assert task["next_run_at"] > before
    assert task["run_count"] == 1


def test_runner_exception_pauses_inflight_occurrence_as_unknown(isolated_scheduler, monkeypatch: pytest.MonkeyPatch) -> None:
    database, tasks = isolated_scheduler
    task_id = _insert_due_task(database)
    claimed = tasks.claim_due(task_id, now_ts=database.now())
    assert claimed is not None

    from app.core import account_access, local_sessions, storage

    monkeypatch.setattr(account_access, "account_execution_error", lambda _account: "")
    monkeypatch.setattr(local_sessions, "store", database)
    monkeypatch.setattr(storage, "store", database)

    class CrashingSkill:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("connection closed after remote write")

    monkeypatch.setattr(scheduler, "LarkCLISkill", lambda: CrashingSkill())
    asyncio.run(scheduler.ScheduledTaskRunner()._run_task(claimed))
    assert database.query_one("SELECT status FROM scheduled_tasks WHERE id = ?", (task_id,))["status"] == "paused"
    assert database.query_one("SELECT status FROM scheduled_task_runs WHERE id = ?", (claimed["scheduled_task_run_id"],))["status"] == "unknown"


@pytest.mark.parametrize("run_state,records", [
    ("service_error", []),
    ("failed", [{"expected": "write", "status": "unknown", "success": False}]),
])
def test_runner_service_error_result_pauses_inflight_occurrence_as_unknown(isolated_scheduler, monkeypatch: pytest.MonkeyPatch, run_state, records) -> None:
    database, tasks = isolated_scheduler
    task_id = _insert_due_task(database)
    claimed = tasks.claim_due(task_id, now_ts=database.now())
    assert claimed is not None

    from app.core import account_access, local_sessions, storage

    monkeypatch.setattr(account_access, "account_execution_error", lambda _account: "")
    monkeypatch.setattr(local_sessions, "store", database)
    monkeypatch.setattr(storage, "store", database)
    monkeypatch.setattr(
        scheduler,
        "LarkCLISkill",
        lambda: SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(
                success=False,
                message="AI 执行服务本次出现异常",
                data={"run_state": run_state, "executed_commands": records},
            ))
        ),
    )

    asyncio.run(scheduler.ScheduledTaskRunner()._run_task(claimed))
    assert database.query_one("SELECT status FROM scheduled_tasks WHERE id = ?", (task_id,))["status"] == "paused"
    assert database.query_one("SELECT status FROM scheduled_task_runs WHERE id = ?", (claimed["scheduled_task_run_id"],))["status"] == "unknown"


def test_only_lease_owner_can_start_recovery(isolated_scheduler, monkeypatch: pytest.MonkeyPatch) -> None:
    database, _tasks = isolated_scheduler
    recoveries: list[int] = []
    monkeypatch.setattr(scheduler.scheduled_task_store, "recover_running", lambda: recoveries.append(1) or 0)

    async def run() -> None:
        first = scheduler.ScheduledTaskRunner(interval_seconds=1, lease_ttl_seconds=60)
        second = scheduler.ScheduledTaskRunner(interval_seconds=1, lease_ttl_seconds=60)
        assert first.start() is True
        assert second.start() is False
        await asyncio.sleep(0)
        await second.stop()
        await first.stop()

    asyncio.run(run())
    assert recoveries == [1]
    assert database.query_one("SELECT * FROM scheduler_leases") is None


def test_run_due_stops_when_database_lease_validation_fails(isolated_scheduler, monkeypatch: pytest.MonkeyPatch) -> None:
    _database, _tasks = isolated_scheduler
    runner = scheduler.ScheduledTaskRunner()
    runner._lease_held = True
    monkeypatch.setattr(scheduler.scheduled_task_store, "runner_lease_valid", lambda _owner: (_ for _ in ()).throw(RuntimeError("db unavailable")))
    monkeypatch.setattr(scheduler.scheduled_task_config_store, "enabled", lambda: pytest.fail("must fail closed before config"))

    asyncio.run(runner.run_due_once())
    assert runner._lease_held is False


def test_heartbeat_database_error_loses_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = scheduler.ScheduledTaskRunner()
    runner._stop_event = asyncio.Event()
    runner._lease_held = True
    monkeypatch.setattr(scheduler.scheduled_task_store, "renew_runner_lease", lambda *_args: (_ for _ in ()).throw(RuntimeError("db unavailable")))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    asyncio.run(runner._heartbeat_loop())
    assert runner._lease_held is False


def test_run_due_stops_before_claim_when_due_query_fails(isolated_scheduler, monkeypatch: pytest.MonkeyPatch) -> None:
    _database, _tasks = isolated_scheduler
    runner = scheduler.ScheduledTaskRunner()
    runner._lease_held = True
    monkeypatch.setattr(scheduler.scheduled_task_store, "runner_lease_valid", lambda _owner: True)
    monkeypatch.setattr(scheduler.scheduled_task_config_store, "enabled", lambda: True)
    monkeypatch.setattr(scheduler.scheduled_task_store, "due_tasks", lambda: (_ for _ in ()).throw(RuntimeError("db unavailable")))

    asyncio.run(runner.run_due_once())
    assert runner._lease_held is False
