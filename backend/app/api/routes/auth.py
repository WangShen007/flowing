from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.login_guard import check_login_rate, clear_account_attempts
from app.core.passwords import hash_password, needs_upgrade, verify_password
from app.core.storage import store

router = APIRouter()

class LoginRequest(BaseModel):
    account: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class AccountInfo(BaseModel):
    account: str
    name: str
    role: Literal["employee", "lead", "admin"] = "employee"


def _normalize_account(account: str) -> str:
    return (account or "").strip()


def ensure_default_accounts() -> None:
    settings = get_settings()
    if not settings.AUTH_BOOTSTRAP_PASSWORD:
        return
    row = store.query_one("SELECT COUNT(*) AS count FROM accounts")
    if row and int(row["count"] or 0) > 0:
        return

    account = settings.AUTH_BOOTSTRAP_ACCOUNT.strip()
    if not account or len(account) > 200 or not 12 <= len(settings.AUTH_BOOTSTRAP_PASSWORD) <= 1024:
        raise HTTPException(status_code=503, detail="初始化管理员配置无效：请设置账号和至少 12 位密码。")
    encoded = hash_password(settings.AUTH_BOOTSTRAP_PASSWORD)
    now = store.now()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
            return
        conn.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", (account, account, encoded, now, now))
        conn.execute(
            "INSERT INTO account_memberships(account, role, updated_at) VALUES (?, ?, ?)",
            (account, "admin", now),
        )


def _extract_token(authorization: Optional[str], x_auth_token: Optional[str]) -> str:
    if x_auth_token:
        return x_auth_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def get_current_account_optional(
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None),
) -> Optional[AccountInfo]:
    ensure_default_accounts()
    token = _extract_token(authorization, x_auth_token)
    if not token:
        return None
    row = store.query_one(
        """SELECT a.account, a.name, COALESCE(m.role, 'employee') AS role
           FROM auth_sessions s JOIN accounts a ON a.account = s.account
           LEFT JOIN account_memberships m ON m.account = a.account
           WHERE s.token = ? AND COALESCE(m.enabled, 1) = 1 AND s.created_at > ?""",
        (token, store.now() - max(1, get_settings().AUTH_SESSION_DAYS) * 86400),
    )
    if not row:
        return None
    return AccountInfo(account=row["account"], name=row["name"] or row["account"], role=row["role"])


def get_current_account(
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None),
) -> AccountInfo:
    account = get_current_account_optional(authorization, x_auth_token)
    if not account:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return account


def get_admin_account(account: AccountInfo = Depends(get_current_account)) -> AccountInfo:
    if account.role != "admin":
        raise HTTPException(status_code=403, detail="此操作需要管理员权限")
    return account


@router.post("/auth/login")
def login(request: LoginRequest, http_request: Request) -> dict:
    ensure_default_accounts()
    account_name = _normalize_account(request.account)
    # Do not trust client-supplied forwarding headers. Proxy trust is configured at the server.
    check_login_rate(store, account_name, http_request.client.host if http_request.client else "unknown")
    row = store.query_one(
        """SELECT a.account, a.name, a.password_hash, COALESCE(m.role, 'employee') AS role
           FROM accounts a LEFT JOIN account_memberships m ON m.account = a.account
           WHERE a.account = ? AND COALESCE(m.enabled, 1) = 1""",
        (account_name,),
    )
    if not row or not verify_password(request.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid account or password")
    if needs_upgrade(row["password_hash"]):
        store.execute("UPDATE accounts SET password_hash = ?, updated_at = ? WHERE account = ? AND password_hash = ?",
                      (hash_password(request.password), store.now(), account_name, row["password_hash"]))
    clear_account_attempts(store, account_name)
    return create_auth_session(row["account"], row["name"] or row["account"], row["role"])


def create_auth_session(account: str, name: str, role: str) -> dict:
    token = secrets.token_urlsafe(32)
    issued_at = datetime.now().isoformat()
    store.execute(
        """
        INSERT INTO auth_sessions(token, account, name, issued_at, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (token, account, name, issued_at, store.now()),
    )
    return {
        "code": 0,
        "data": {
            "token": token,
            "account": {
                "account": account,
                "name": name,
                "role": role,
            },
        },
    }


@router.get("/auth/me")
async def me(account: AccountInfo = Depends(get_current_account)):
    return {"code": 0, "data": account.model_dump()}


@router.post("/auth/logout")
async def logout(
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None),
) -> dict:
    token = _extract_token(authorization, x_auth_token)
    if token:
        store.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
    return {"code": 0, "message": "ok"}


class MemberUpdate(BaseModel):
    role: Literal["employee", "lead", "admin"] | None = None
    enabled: bool | None = None


@router.get("/admin/members")
async def list_members(_admin: AccountInfo = Depends(get_admin_account)) -> dict:
    rows = store.query_all(
        """SELECT a.account, a.name, COALESCE(m.role, 'employee') AS role,
                  COALESCE(m.enabled, 1) AS enabled
           FROM accounts a LEFT JOIN account_memberships m ON m.account = a.account
           ORDER BY a.created_at, a.account"""
    )
    return {"code": 0, "data": [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]}


@router.patch("/admin/members/{member}")
async def update_member(
    member: str, request: MemberUpdate, admin: AccountInfo = Depends(get_admin_account),
) -> dict:
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Recheck inside the transaction so concurrent demotions cannot bypass policy.
        actor = conn.execute(
            "SELECT role, enabled FROM account_memberships WHERE account = ?", (admin.account,),
        ).fetchone()
        if not actor or actor["role"] != "admin" or not actor["enabled"]:
            raise HTTPException(status_code=403, detail="此操作需要管理员权限")
        row = conn.execute(
            """SELECT a.account, COALESCE(m.role, 'employee') AS role,
                      COALESCE(m.enabled, 1) AS enabled
               FROM accounts a LEFT JOIN account_memberships m ON m.account = a.account
               WHERE a.account = ?""", (member,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="账号不存在")
        role = request.role if request.role is not None else row["role"]
        enabled = request.enabled if request.enabled is not None else bool(row["enabled"])
        if row["role"] == "admin" and row["enabled"] and (role != "admin" or not enabled):
            remaining = conn.execute(
                "SELECT COUNT(*) FROM account_memberships WHERE role = 'admin' AND enabled = 1 AND account != ?",
                (member,),
            ).fetchone()[0]
            if not remaining:
                raise HTTPException(status_code=409, detail="必须保留至少一名启用的管理员")
        conn.execute(
            """INSERT INTO account_memberships(account, role, enabled, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(account) DO UPDATE SET role=excluded.role,
                   enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (member, role, int(enabled), store.now()),
        )
        if not enabled:
            conn.execute("DELETE FROM auth_sessions WHERE account = ?", (member,))
            # Channel schema may not yet exist on an older installation. Revoke
            # atomically with disabling so a quick re-enable cannot revive queued work.
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_bindings'").fetchone():
                conn.execute("DELETE FROM workflow_checkpoints WHERE user_id=? AND session_id IN "
                             "(SELECT session_id FROM bot_bindings WHERE account=?)",(member,member))
                conn.execute("DELETE FROM bot_bindings WHERE account=?",(member,))
                conn.execute("DELETE FROM bot_pairings WHERE account=?",(member,))
        conn.execute(
            "INSERT INTO account_audit(actor, subject, action, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (admin.account, member, "membership_updated", store.dumps({
                "before": {"role": row["role"], "enabled": bool(row["enabled"])},
                "after": {"role": role, "enabled": enabled},
            }), store.now()),
        )
    return {"code": 0, "data": {"account": member, "role": role, "enabled": enabled}}
