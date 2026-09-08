import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import scenarios, templates
from app.api.routes.auth import AccountInfo, get_current_account
from app.core import template_store
from app.core.storage import SQLiteStore


@pytest.mark.parametrize("partial", [False, True])
def test_legacy_schema_upgrade_preserves_versions(tmp_path: Path, partial: bool) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE user_template_versions (
                id INTEGER PRIMARY KEY, template_id INTEGER, version INTEGER,
                prompt TEXT, fields_json TEXT
            );
            INSERT INTO user_template_versions VALUES (7, 3, 2, 'original', '[]');
        """)
        if partial:
            conn.execute(
                "ALTER TABLE user_template_versions ADD COLUMN requires_ai_content_generation INTEGER DEFAULT 0"
            )
            conn.execute("UPDATE user_template_versions SET requires_ai_content_generation = 1")
    upgraded = SQLiteStore(path)
    SQLiteStore(path)
    row = upgraded.query_one("SELECT * FROM user_template_versions WHERE id = 7")
    assert row is not None
    assert (row["template_id"], row["version"], row["prompt"]) == (3, 2, "original")
    assert row["requires_ai_content_generation"] == int(partial)
    assert row["content_generation_label"] == ""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = SQLiteStore(tmp_path / "fresh.sqlite3")
    for name in ("owner", "reader"):
        database.execute("INSERT INTO accounts VALUES (?, ?, 'unused', 1, 1)", (name, name))
    monkeypatch.setattr(template_store, "store", database)
    app = FastAPI()
    app.include_router(templates.router)
    app.include_router(scenarios.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="owner", name="owner")
    with TestClient(app) as test_client:
        yield test_client


def test_template_lifecycle_and_access(client: TestClient) -> None:
    payload = {
        "title": "Weekly",
        "category": "test",
        "description": "first",
        "prompt": "Summarize {{topic}}",
        "fields": [{"key": "topic", "label": "Topic"}],
        "requires_ai_content_generation": True,
        "content_generation_label": "Expand report",
    }
    response = client.post("/templates", json=payload)
    assert response.status_code == 200
    original = response.json()["data"]
    template_id = original["template_id"]
    assert original["requires_ai_content_generation"] is True
    assert len(client.get("/templates?scope=mine").json()["data"]) == 1
    assert any(item["id"] == original["id"] for item in client.get("/scenarios").json()["data"])
    render = {"template_id": original["id"], "values": {}, "enable_ai_content_generation": False}
    missing = client.post("/scenarios/render", json=render).json()["data"]
    assert missing["executable"] is False
    assert missing["missing_fields"][0]["key"] == "topic"
    render["values"] = {"topic": "release"}
    rendered = client.post("/scenarios/render", json=render).json()["data"]
    assert rendered["executable"] is True
    assert "Summarize release" in rendered["message"]
    response = client.put(
        f"/templates/{template_id}",
        json={
            **payload,
            "prompt": "Updated {{topic}}",
            "requires_ai_content_generation": False,
            "content_generation_label": "",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["current_version"] == 2
    rollback = client.post(f"/templates/{template_id}/versions/1/rollback").json()["data"]
    assert rollback["current_version"] == 3
    assert rollback["prompt"] == payload["prompt"]
    assert rollback["requires_ai_content_generation"] is True
    assert rollback["content_generation_label"] == "Expand report"
    versions = client.get(f"/templates/{template_id}/versions").json()["data"]
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert [v["is_current"] for v in versions] == [True, False, False]
    owner = client.app.dependency_overrides[get_current_account]
    client.app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="reader", name="reader")
    assert client.get("/templates").json()["data"] == []
    assert client.get(f"/templates/{template_id}/versions").status_code == 403
    assert client.post("/scenarios/render", json=render).status_code == 404
    client.app.dependency_overrides[get_current_account] = owner
    assert client.post(f"/templates/{template_id}/publish").json()["data"]["visibility"] == "public"
    client.app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="reader", name="reader")
    assert len(client.get("/templates?scope=community").json()["data"]) == 1
    assert client.post("/scenarios/render", json=render).status_code == 200
    assert client.put(f"/templates/{template_id}", json=payload).status_code == 403
    assert client.post(f"/templates/{template_id}/versions/1/rollback").status_code == 403
