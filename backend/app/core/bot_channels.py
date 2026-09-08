"""Private channel bindings and durable inbox. No administrator ownership bypass."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from contextvars import ContextVar
from typing import Any

from app.core.feishu_tokens import decrypt_token, encrypt_token
from app.core.storage import SQLiteStore, store

channel_execution: ContextVar[str] = ContextVar("channel_execution", default="")


class ChannelStore:
    def __init__(self, db: SQLiteStore):
        self.db = db
        with db.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS bot_bindings (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(account) ON DELETE CASCADE,
                    channel TEXT NOT NULL CHECK(channel IN ('weixin','telegram')),
                    bot_id TEXT NOT NULL, peer_id TEXT NOT NULL, credential TEXT NOT NULL,
                    session_id TEXT NOT NULL, created_at INTEGER NOT NULL,
                    UNIQUE(channel,bot_id,peer_id), UNIQUE(account,channel)
                );
                CREATE TABLE IF NOT EXISTS bot_pairings (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(account) ON DELETE CASCADE,
                    channel TEXT NOT NULL, secret_hash TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, expires_at INTEGER NOT NULL,
                    UNIQUE(account,channel)
                );
                CREATE TABLE IF NOT EXISTS bot_inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    binding_id TEXT NOT NULL REFERENCES bot_bindings(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL, text TEXT NOT NULL, context TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', reply TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL, UNIQUE(binding_id,event_id)
                );
                CREATE INDEX IF NOT EXISTS bot_inbox_status ON bot_inbox(status,id);
                CREATE TABLE IF NOT EXISTS bot_cursors (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bot_approvals (
                    code_hash TEXT PRIMARY KEY,
                    binding_id TEXT NOT NULL REFERENCES bot_bindings(id) ON DELETE CASCADE,
                    resume_id TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_health (
                    binding_id TEXT PRIMARY KEY REFERENCES bot_bindings(id) ON DELETE CASCADE,
                    state TEXT NOT NULL, checked_at INTEGER NOT NULL
                );
            """)

    def active(self, binding_id: str, account: str = "") -> dict[str, Any] | None:
        row = self.db.query_one(
            """SELECT b.*, a.name, COALESCE(m.role,'employee') AS role
            FROM bot_bindings b JOIN accounts a ON a.account=b.account
            LEFT JOIN account_memberships m ON m.account=b.account
            WHERE b.id=? AND COALESCE(m.enabled,1)=1""",
            (binding_id,),
        )
        return dict(row) if row and (not account or row["account"] == account) else None

    def list_owned(self, account: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.query_all(
                "SELECT b.id,b.channel,b.peer_id,b.session_id,b.created_at,COALESCE(h.state,'connecting') AS state, "
                "h.checked_at,(SELECT COUNT(*) FROM bot_inbox i WHERE i.binding_id=b.id "
                "AND i.status IN ('execution_unknown','delivery_unknown')) AS uncertain_count "
                "FROM bot_bindings b LEFT JOIN bot_health h ON h.binding_id=b.id WHERE b.account=?",
                (account,),
            )
        ]

    def health(self, binding_id: str, state: str) -> None:
        self.db.execute(
            "INSERT INTO bot_health SELECT id,?,? FROM bot_bindings WHERE id=? "
            "ON CONFLICT(binding_id) DO UPDATE SET state=excluded.state,checked_at=excluded.checked_at",
            (state, self.db.now(), binding_id),
        )

    def new_pairing(self, account: str, channel: str, payload: dict[str, Any]) -> tuple[str, str]:
        pairing_id, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM bot_pairings WHERE expires_at<=? OR (account=? AND channel=?)",
                (self.db.now(), account, channel),
            )
            conn.execute(
                "INSERT INTO bot_pairings VALUES (?,?,?,?,?,?,?)",
                (
                    pairing_id,
                    account,
                    channel,
                    hashlib.sha256(nonce.encode()).hexdigest(),
                    encrypt_token(f"bot-pair:{account}:{pairing_id}", payload),
                    "waiting",
                    self.db.now() + 300,
                ),
            )
        return pairing_id, nonce

    def pairing(self, account: str, pairing_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM bot_pairings WHERE account=? AND id=? AND expires_at>?", (account, pairing_id, self.db.now())
        )
        if not row:
            return None
        result = dict(row)
        result["payload"] = decrypt_token(f"bot-pair:{account}:{pairing_id}", row["payload"])
        return result

    def update_pairing(self, pairing: dict[str, Any], payload: dict[str, Any], status: str) -> None:
        self.db.execute(
            "UPDATE bot_pairings SET payload=?,status=? WHERE id=? AND account=? AND expires_at>?",
            (
                encrypt_token(f"bot-pair:{pairing['account']}:{pairing['id']}", payload),
                status,
                pairing["id"],
                pairing["account"],
                self.db.now(),
            ),
        )

    def telegram_candidate(self, nonce: str, bot_id: str, peer: str) -> None:
        row = self.db.query_one(
            "SELECT * FROM bot_pairings WHERE channel='telegram' AND secret_hash=? AND expires_at>?",
            (hashlib.sha256(nonce.encode()).hexdigest(), self.db.now()),
        )
        if not row or row["status"] != "waiting":
            return
        p = self.pairing(row["account"], row["id"])
        if not p or p["payload"]["bot_id"] != bot_id:
            return
        payload = {**p["payload"], "peer_id": peer}
        self.db.execute(
            "UPDATE bot_pairings SET payload=?,status='scanned' WHERE id=? AND status='waiting'",
            (encrypt_token(f"bot-pair:{p['account']}:{p['id']}", payload), p["id"]),
        )

    def confirm(self, account: str, pairing_id: str) -> str:
        p = self.pairing(account, pairing_id)
        if not p or p["status"] != "scanned":
            raise ValueError("绑定未就绪或已过期，请重新扫码")
        payload = p["payload"]
        binding_id = str(uuid.uuid4())
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,expires_at FROM bot_pairings WHERE id=? AND account=?", (pairing_id, account)
            ).fetchone()
            enabled = conn.execute(
                "SELECT 1 FROM accounts a LEFT JOIN account_memberships m ON m.account=a.account "
                "WHERE a.account=? AND COALESCE(m.enabled,1)=1",
                (account,),
            ).fetchone()
            if not enabled or not row or row["status"] != "scanned" or row["expires_at"] <= self.db.now():
                raise ValueError("绑定已失效")
            if conn.execute(
                "SELECT 1 FROM bot_bindings WHERE (channel=? AND bot_id=? AND peer_id=?) OR (account=? AND channel=?)",
                (p["channel"], payload["bot_id"], payload["peer_id"], account, p["channel"]),
            ).fetchone():
                raise ValueError("该渠道已绑定，请先在原账号解绑；不能覆盖其他人的绑定")
            conn.execute(
                "INSERT INTO bot_bindings VALUES (?,?,?,?,?,?,?,?)",
                (
                    binding_id,
                    account,
                    p["channel"],
                    payload["bot_id"],
                    payload["peer_id"],
                    encrypt_token(f"bot-binding:{account}:{binding_id}", payload),
                    str(uuid.uuid4()),
                    self.db.now(),
                ),
            )
            conn.execute("DELETE FROM bot_pairings WHERE id=?", (pairing_id,))
        return binding_id

    def credentials(self, binding: dict[str, Any]) -> dict[str, Any]:
        return decrypt_token(f"bot-binding:{binding['account']}:{binding['id']}", binding["credential"])

    def unbind(self, account: str, binding_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT session_id FROM bot_bindings WHERE id=? AND account=?", (binding_id, account)
            ).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM workflow_checkpoints WHERE user_id=? AND session_id=?", (account, row["session_id"])
                )
                conn.execute("DELETE FROM bot_bindings WHERE id=? AND account=?", (binding_id, account))
                conn.execute("DELETE FROM bot_cursors WHERE id=?", (binding_id,))

    def enqueue(self, binding_id: str, event_id: str, text: str, context: str = "") -> None:
        if not event_id or not self.active(binding_id):
            return
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM bot_inbox WHERE binding_id=? AND event_id=?", (binding_id, event_id)
            ).fetchone():
                return
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM bot_inbox WHERE binding_id=? AND status='queued'", (binding_id,)
                ).fetchone()[0]
                >= 20
            ):
                raise ValueError("渠道队列已满")
            conn.execute(
                "INSERT INTO bot_inbox(binding_id,event_id,text,context,created_at) VALUES (?,?,?,?,?)",
                (
                    binding_id,
                    event_id,
                    text[:32000],
                    encrypt_token(f"bot-context:{binding_id}", {"token": context}),
                    self.db.now(),
                ),
            )

    def cursor(self, key: str) -> str:
        row = self.db.query_one("SELECT value FROM bot_cursors WHERE id=?", (key,))
        return row["value"] if row else ""

    def set_cursor(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO bot_cursors VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (key, value)
        )

    def approval(self, binding_id: str, resume_id: str) -> str:
        code = secrets.token_urlsafe(18)
        self.db.execute(
            "INSERT INTO bot_approvals VALUES (?,?,?,?)",
            (hashlib.sha256(code.encode()).hexdigest(), binding_id, resume_id, self.db.now() + 600),
        )
        return code

    def take_approval(self, binding_id: str, code: str) -> str:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            key = hashlib.sha256(code.encode()).hexdigest()
            row = conn.execute(
                "SELECT resume_id FROM bot_approvals WHERE code_hash=? AND binding_id=? AND expires_at>?",
                (key, binding_id, self.db.now()),
            ).fetchone()
            if not row:
                raise ValueError("确认码已失效或不属于此对话，请在网站查看任务")
            conn.execute("DELETE FROM bot_approvals WHERE code_hash=?", (key,))
            return row["resume_id"]


channel_store = ChannelStore(store)
