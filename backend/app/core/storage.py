from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("FEISHU_CLI_DATA_DIR") or ROOT_DIR / ".feishu_cli_data")
DB_PATH = DATA_DIR / "feishu_cli_web.sqlite3"
LARK_CLI_PROFILES_DIR = DATA_DIR / "lark_cli_profiles"
LARK_CLI_USERS_DIR = DATA_DIR / "lark_cli_users"


class SQLiteStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LARK_CLI_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        LARK_CLI_USERS_DIR.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    name TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(account) REFERENCES accounts(account) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS account_memberships (
                    account TEXT PRIMARY KEY,
                    role TEXT NOT NULL DEFAULT 'employee'
                        CHECK(role IN ('employee', 'lead', 'admin')),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(account) REFERENCES accounts(account) ON DELETE CASCADE
                );

                INSERT OR IGNORE INTO account_memberships(account, role, enabled, updated_at)
                    SELECT account, CASE WHEN account = 'admin' THEN 'admin' ELSE 'employee' END,
                        1, updated_at FROM accounts;

                CREATE TABLE IF NOT EXISTS account_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feishu_identities (
                    account TEXT PRIMARY KEY REFERENCES accounts(account) ON DELETE CASCADE,
                    app_id TEXT NOT NULL,
                    tenant_key TEXT NOT NULL,
                    open_id TEXT NOT NULL,
                    UNIQUE(app_id, tenant_key, open_id)
                );

                CREATE TABLE IF NOT EXISTS feishu_grants (
                    account TEXT PRIMARY KEY REFERENCES accounts(account) ON DELETE CASCADE,
                    encrypted_token TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    refresh_expires_at INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feishu_oauth_flows (
                    state_hash TEXT PRIMARY KEY,
                    browser_hash TEXT NOT NULL,
                    verifier TEXT NOT NULL,
                    account TEXT,
                    expires_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_auth_epochs (
                    account TEXT PRIMARY KEY REFERENCES accounts(account) ON DELETE CASCADE,
                    epoch INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(session_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(session_id, user_id)
                        REFERENCES chat_sessions(session_id, user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(session_id, user_id)
                        REFERENCES chat_sessions(session_id, user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated
                    ON chat_sessions(user_id, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                    ON chat_messages(user_id, session_id, created_at ASC);

                CREATE TABLE IF NOT EXISTS execution_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    request TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    executed_commands_json TEXT NOT NULL DEFAULT '[]',
                    success INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_execution_records_session
                    ON execution_records(user_id, session_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS workflow_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    intent_key TEXT NOT NULL,
                    request_pattern TEXT NOT NULL,
                    last_request_hash TEXT NOT NULL,
                    label TEXT NOT NULL,
                    blueprint_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'candidate'
                        CHECK(status IN ('candidate', 'active', 'disabled')),
                    success_count INTEGER NOT NULL DEFAULT 1,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_execution_record_id INTEGER,
                    last_used_at INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(user_id, intent_key, request_pattern),
                    FOREIGN KEY(last_execution_record_id) REFERENCES execution_records(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_workflow_memories_user_status
                    ON workflow_memories(user_id, status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS profile_states (
                    profile TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    original_request TEXT NOT NULL,
                    task_message TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    time_of_day TEXT NOT NULL DEFAULT '',
                    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                    next_run_at INTEGER NOT NULL,
                    last_run_at INTEGER,
                    status TEXT NOT NULL DEFAULT 'active',
                    run_count INTEGER NOT NULL DEFAULT 0,
                    max_runs INTEGER,
                    last_result_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due
                    ON scheduled_tasks(status, next_run_at);

                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user
                    ON scheduled_tasks(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS scheduled_task_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    occurrence_key TEXT NOT NULL,
                    scheduled_for INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running'
                        CHECK(status IN ('running', 'succeeded', 'failed', 'unknown')),
                    result_json TEXT NOT NULL DEFAULT '{}',
                    started_at INTEGER NOT NULL,
                    finished_at INTEGER,
                    UNIQUE(task_id, occurrence_key),
                    FOREIGN KEY(task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_task
                    ON scheduled_task_runs(task_id, started_at DESC);

                CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_status
                    ON scheduled_task_runs(status, started_at ASC);

                CREATE TABLE IF NOT EXISTS scheduler_leases (
                    lease_key TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    acquired_at INTEGER NOT NULL,
                    heartbeat_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT 'private',
                    owner_account TEXT NOT NULL,
                    owner_name TEXT NOT NULL,
                    current_version INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    published_at INTEGER,
                    FOREIGN KEY(owner_account) REFERENCES accounts(account) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_user_templates_owner
                    ON user_templates(owner_account, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_user_templates_visibility
                    ON user_templates(visibility, updated_at DESC);

                CREATE TABLE IF NOT EXISTS user_template_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    fields_json TEXT NOT NULL DEFAULT '[]',
                    requires_ai_content_generation INTEGER NOT NULL DEFAULT 0,
                    content_generation_label TEXT NOT NULL DEFAULT '',
                    editor_account TEXT NOT NULL,
                    editor_name TEXT NOT NULL,
                    change_note TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    UNIQUE(template_id, version),
                    FOREIGN KEY(template_id) REFERENCES user_templates(id) ON DELETE CASCADE,
                    FOREIGN KEY(editor_account) REFERENCES accounts(account) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_user_template_versions_template
                    ON user_template_versions(template_id, version DESC);
                """
            )

            # Serialize the upgrade across processes and preserve existing versions.
            conn.execute("BEGIN IMMEDIATE")
            flow_columns = {row["name"] for row in conn.execute("PRAGMA table_info(feishu_oauth_flows)")}
            if "account_epoch" not in flow_columns:
                conn.execute("ALTER TABLE feishu_oauth_flows ADD COLUMN account_epoch INTEGER NOT NULL DEFAULT 0")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(user_template_versions)")}
            if "requires_ai_content_generation" not in columns:
                conn.execute(
                    "ALTER TABLE user_template_versions "
                    "ADD COLUMN requires_ai_content_generation INTEGER NOT NULL DEFAULT 0"
                )
            if "content_generation_label" not in columns:
                conn.execute(
                    "ALTER TABLE user_template_versions "
                    "ADD COLUMN content_generation_label TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value if value is not None else {}, ensure_ascii=False)

    @staticmethod
    def loads(value: str | None, fallback: Any) -> Any:
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return fallback

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(sql, tuple(params))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a small group of statements as one cross-process SQLite transaction.

        The existing helpers intentionally open one connection per operation.  A
        scheduler claim needs an atomic state update and occurrence insert, so it
        must use one connection and an IMMEDIATE transaction.  The database
        lock, rather than the in-process mutex, is what also serializes separate
        worker processes using the same SQLite file.
        """

        with self._lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def execute_insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock, self.connect() as conn:
            cursor = conn.execute(sql, tuple(params))
            return int(cursor.lastrowid)

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock, self.connect() as conn:
            return conn.execute(sql, tuple(params)).fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock, self.connect() as conn:
            return list(conn.execute(sql, tuple(params)).fetchall())

    def now(self) -> int:
        return int(time.time())


store = SQLiteStore()
