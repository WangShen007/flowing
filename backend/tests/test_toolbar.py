from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import scheduled_tasks
from app.api.routes.auth import AccountInfo, get_current_account
from app.core import model_config
from app.core.storage import SQLiteStore


@pytest.mark.parametrize(
    "status,expected",
    [
        ("paused", 200),
        ("completed", 200),
        ("failed", 200),
        ("active", 409),
        ("running", 409),
    ],
)
def test_task_deletion_states(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, expected: int) -> None:
    database = SQLiteStore(tmp_path / "tasks.sqlite3")
    database.execute(
        "INSERT INTO scheduled_tasks (user_id, session_id, original_request, task_message, schedule_type, "
        "next_run_at, status, created_at, updated_at) VALUES ('admin','test','test','test','once',1,?,1,1)",
        (status,),
    )
    monkeypatch.setattr(scheduled_tasks, "store", database)
    app = FastAPI()
    app.include_router(scheduled_tasks.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="other", name="Other")
    with TestClient(app) as client:
        assert client.delete("/scheduled-tasks/1").status_code == 404
        app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="admin", name="Admin")
        assert client.delete("/scheduled-tasks/1").status_code == expected
    assert (database.query_one("SELECT id FROM scheduled_tasks") is None) == (expected == 200)


def test_unknown_scheduled_result_requires_explicit_resume_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = SQLiteStore(tmp_path / "tasks.sqlite3")
    database.execute(
        "INSERT INTO scheduled_tasks (user_id, session_id, original_request, task_message, schedule_type, "
        "next_run_at, status, last_result_json, created_at, updated_at) "
        "VALUES ('admin','test','test','test','daily',1,'paused',?,1,1)",
        (database.dumps({"status": "unknown", "requires_confirmation": True}),),
    )
    monkeypatch.setattr(scheduled_tasks, "store", database)
    app = FastAPI()
    app.include_router(scheduled_tasks.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="admin", name="Admin")
    with TestClient(app) as client:
        response = client.post("/scheduled-tasks/1/resume")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "scheduled_task_result_unknown"
        response = client.post("/scheduled-tasks/1/resume", json={"confirm_unknown": True})
        assert response.status_code == 200
    task = database.query_one("SELECT status, last_result_json FROM scheduled_tasks WHERE id = 1")
    assert task["status"] == "active"
    assert database.loads(task["last_result_json"], {})["recovery_acknowledged"] is True


def test_model_save_keeps_key_when_blank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=test-only-key\n", encoding="utf-8")
    monkeypatch.setattr(model_config, "ENV_PATH", path)
    monkeypatch.setattr(model_config, "current_model_config", dict)
    model_config.apply_model_config(model_config.ModelConfigRequest(preset="deepseek"))
    saved = model_config._read_env_values()
    assert saved["OPENAI_API_KEY"] == "test-only-key"
    assert saved["LLM_MODEL"] == "deepseek-v4-flash"
    assert saved["OPENAI_BASE_URL"] == "https://api.deepseek.com"
