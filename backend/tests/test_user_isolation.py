from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import local_sessions
from app.core.local_sessions import LocalSessionStore
from app.core.storage import SQLiteStore


@pytest.mark.parametrize("left,right", [("a.b", "a-b"), ("---", "local"), ("张三", "李四")])
def test_distinct_accounts_never_share_messages(tmp_path, monkeypatch, left, right):
    database = SQLiteStore(tmp_path / "isolation.sqlite3")
    monkeypatch.setattr(local_sessions, "store", database)
    sessions = LocalSessionStore()
    sessions.append_message(left, "private-session", "user", "owner-only")
    assert sessions.get_session(right, "private-session") is None
    assert sessions.get_session(left, "private-session")["messages"][0]["content"] == "owner-only"
    assert LocalSessionStore._safe_user_id(left) == left


@pytest.mark.parametrize("identity", [None, "", 1, "x" * 201])
def test_missing_identity_never_defaults_to_another_account(identity):
    with pytest.raises(ValueError):
        LocalSessionStore._safe_user_id(identity)


def test_concurrent_accounts_keep_messages_isolated(tmp_path, monkeypatch):
    database = SQLiteStore(tmp_path / "concurrent.sqlite3")
    monkeypatch.setattr(local_sessions, "store", database)
    sessions = LocalSessionStore()
    accounts = [f"user.{index}" for index in range(24)]

    def write(account):
        sessions.append_message(account, f"session-{account}", "user", account)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, accounts))
    for account in accounts:
        session = sessions.get_session(account, f"session-{account}")
        assert [message["content"] for message in session["messages"]] == [account]


def test_append_reads_history_once_and_keeps_list_counts(tmp_path, monkeypatch):
    database = SQLiteStore(tmp_path / "history.sqlite3")
    monkeypatch.setattr(local_sessions, "store", database)
    sessions = LocalSessionStore()
    calls = []
    original = sessions._messages_for

    def observed(user, session):
        calls.append((user, session))
        return original(user, session)

    monkeypatch.setattr(sessions, "_messages_for", observed)
    result = sessions.append_message("owner", "", "user", "first")
    assert len(calls) == 1
    sessions.append_message("owner", result["session_id"], "assistant", "second")
    assert len(calls) == 2
    listing = sessions.list_sessions("owner", limit=1)
    assert listing[0]["message_count"] == 2
    assert listing[0]["title"] == "first"


def test_simultaneous_session_creation_is_idempotent(tmp_path, monkeypatch):
    from threading import Barrier

    database = SQLiteStore(tmp_path / "session-race.sqlite3")
    monkeypatch.setattr(local_sessions, "store", database)
    sessions = LocalSessionStore()
    original = database.query_one
    barrier = Barrier(2)

    def racing_query(sql, params=()):
        row = original(sql, params)
        if sql.startswith("SELECT * FROM chat_sessions") and row is None:
            barrier.wait(timeout=5)
        return row

    monkeypatch.setattr(database, "query_one", racing_query)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sessions.get_or_create("owner", "same"), range(2)))
    assert [result["session_id"] for result in results] == ["same", "same"]
