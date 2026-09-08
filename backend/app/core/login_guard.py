"""Shared, bounded login-attempt windows, including across server workers."""
from __future__ import annotations

import hashlib

from fastapi import HTTPException

from app.core.storage import SQLiteStore

WINDOW_SECONDS = 600
ACCOUNT_LIMIT = 10
ADDRESS_LIMIT = 60


def check_login_rate(database: SQLiteStore, account: str, address: str) -> None:
    now = database.now()
    buckets = [("account:" + account, ACCOUNT_LIMIT), ("address:" + address, ADDRESS_LIMIT)]
    with database.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE IF NOT EXISTS login_attempt_windows (
            key_hash TEXT PRIMARY KEY, started_at INTEGER NOT NULL, attempts INTEGER NOT NULL)""")
        conn.execute("DELETE FROM login_attempt_windows WHERE started_at <= ?", (now - WINDOW_SECONDS,))
        keys = [(hashlib.sha256(key.encode()).hexdigest(), limit) for key, limit in buckets]
        for key, limit in keys:
            row = conn.execute("SELECT * FROM login_attempt_windows WHERE key_hash = ?", (key,)).fetchone()
            if row and row["attempts"] >= limit:
                raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后重试。",
                                    headers={"Retry-After": str(max(1, row["started_at"] + WINDOW_SECONDS - now))})
        for key, _limit in keys:
            conn.execute("""INSERT INTO login_attempt_windows VALUES (?, ?, 1)
                ON CONFLICT(key_hash) DO UPDATE SET attempts = attempts + 1""", (key, now))


def clear_account_attempts(database: SQLiteStore, account: str) -> None:
    key = hashlib.sha256(("account:" + account).encode()).hexdigest()
    database.execute("DELETE FROM login_attempt_windows WHERE key_hash = ?", (key,))
