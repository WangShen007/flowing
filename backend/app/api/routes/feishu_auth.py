from __future__ import annotations

import asyncio
import hashlib
import secrets
import sqlite3
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.routes.auth import (
    ADMIN_ACCOUNT_DEPENDENCY,
    CURRENT_ACCOUNT_DEPENDENCY,
    OPTIONAL_ACCOUNT_DEPENDENCY,
    AccountInfo,
    create_auth_session,
    get_admin_account,
)
from app.config import get_settings
from app.core import feishu_tokens as tokens
from app.core.feishu_permissions import CAPABILITIES, capabilities_for_role, member_role, scopes_for_role
from app.core.storage import store

router = APIRouter()
INVITATION_TTL_SECONDS = 7 * 24 * 60 * 60


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _invitation_active(row: sqlite3.Row | None, now: int) -> bool:
    return bool(
        row is not None and row["revoked_at"] is None and row["redeemed_at"] is None
        and row["expires_at"] > now
    )


def _require_active_admin(conn: sqlite3.Connection, account: str) -> None:
    row = conn.execute(
        "SELECT role, enabled FROM account_memberships WHERE account = ?", (account,),
    ).fetchone()
    if not row or row["role"] != "admin" or not row["enabled"]:
        raise HTTPException(status_code=403, detail="此操作需要管理员权限")


@router.get("/auth/feishu/config")
async def login_config() -> dict:
    return {"code": 0, "data": {"enabled": tokens.enterprise_configured()}}


class AuthorizationRequest(BaseModel):
    scopes: list[str] = Field(default_factory=list, max_length=200)
    # Re-authorize even when every requested scope is already granted, so an
    # explicit "重新授权" always produces a visible Feishu consent round-trip.
    force: bool = False
    invitation: str | None = Field(default=None, min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


_DEFAULT_AUTHORIZATION_REQUEST = AuthorizationRequest()


@router.post("/auth/feishu/start")
async def start_login(
    request: AuthorizationRequest = _DEFAULT_AUTHORIZATION_REQUEST,
    account: AccountInfo | None = OPTIONAL_ACCOUNT_DEPENDENCY,
) -> dict:
    try:
        tokens.validate_enterprise_config()
    except tokens.FeishuAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    invitation_id = None
    if request.invitation:
        if account:
            raise HTTPException(status_code=400, detail="请退出当前账号后使用邀请链接。")
        invitation = store.query_one(
            "SELECT * FROM account_invitations WHERE token_hash = ?", (_digest(request.invitation),),
        )
        if not _invitation_active(invitation, store.now()):
            raise HTTPException(status_code=410, detail="邀请已失效，请联系管理员重新邀请。")
        invitation_id = invitation["id"]
    allowed = set(scopes_for_role(member_role(account.account) if account else "employee"))
    # A new device first proves identity; business permissions belong to the account.
    requested = set(request.scopes) if request.scopes else (allowed if account else {"offline_access"})
    if requested - allowed:
        raise HTTPException(status_code=403, detail="请求的权限不属于当前角色的企业能力清单")
    requested.add("offline_access")
    if account and not request.force:
        grant = store.query_one("SELECT scopes FROM feishu_grants WHERE account = ? AND revoked = 0", (account.account,))
        if grant:
            try:
                await asyncio.to_thread(tokens.access_token_for_account, account.account)
            except tokens.FeishuAuthError as exc:
                if not exc.reauthorize:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
            else:
                grant = store.query_one("SELECT scopes FROM feishu_grants WHERE account = ?", (account.account,))
                requested -= set(grant["scopes"].split())
                if not requested:
                    return {"code": 0, "data": {"ready": True}}
    # Every new grant must carry refresh credentials, even for incremental consent.
    requested.add("offline_access")
    # Feishu's authorize endpoint does not register a PKCE challenge, and the v3 token
    # endpoint rejects any code_verifier it never saw. This is a confidential client
    # (server-held secret), so PKCE is omitted and the flow is bound to the browser instead.
    state, browser_secret = (secrets.token_urlsafe(32) for _ in range(2))
    store.execute("DELETE FROM feishu_oauth_flows WHERE expires_at <= ?", (store.now(),))
    epoch = store.query_one("SELECT epoch FROM account_auth_epochs WHERE account = ?", (account.account,)) if account else None
    store.execute(
        """INSERT INTO feishu_oauth_flows(state_hash, browser_hash, verifier, account, expires_at,
                   account_epoch, invitation_id) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (_digest(state), _digest(browser_secret), "", account.account if account else None,
         store.now() + 300, epoch["epoch"] if epoch else 0, invitation_id),
    )
    url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode({
        "client_id": get_settings().FEISHU_APP_ID, "response_type": "code",
        "redirect_uri": get_settings().FEISHU_REDIRECT_URI,
        "scope": " ".join(sorted(requested)), "state": state,
    })
    return {"code": 0, "data": {"url": url, "state": state, "browser_secret": browser_secret}}


@router.get("/auth/feishu/status")
async def authorization_status(account: AccountInfo = CURRENT_ACCOUNT_DEPENDENCY) -> dict:
    role = member_role(account.account)
    row = store.query_one("SELECT scopes, expires_at, refresh_expires_at, revoked FROM feishu_grants WHERE account = ?", (account.account,))
    identity = store.query_one("SELECT app_id, tenant_key FROM feishu_identities WHERE account = ?", (account.account,))
    settings = get_settings()
    identity_matches = bool(
        identity and settings.FEISHU_APP_ID and settings.FEISHU_TENANT_KEY
        and identity["app_id"] == settings.FEISHU_APP_ID and identity["tenant_key"] == settings.FEISHU_TENANT_KEY
    )
    granted = set(row["scopes"].split()) if identity_matches and row and not row["revoked"] else set()
    enterprise = {
        "app_id": settings.FEISHU_APP_ID,
        "app_name": settings.FEISHU_APP_NAME.strip() or settings.FEISHU_APP_ID,
        "tenant_key": settings.FEISHU_TENANT_KEY,
        "tenant_name": settings.FEISHU_TENANT_NAME.strip() or settings.FEISHU_TENANT_KEY,
        "identity_app_id": identity["app_id"] if identity else None,
        "identity_tenant_key": identity["tenant_key"] if identity else None,
        "matches_config": identity_matches,
    }
    return {"code": 0, "data": {
        "enabled": tokens.enterprise_configured(), "role": role,
        "connected": bool(identity_matches and row and not row["revoked"] and max(row["expires_at"], row["refresh_expires_at"]) > store.now()),
        "has_grant": bool(row),
        "enterprise": enterprise,
        "configuration_issues": tokens.enterprise_configuration_issues() if role == "admin" else [],
        "granted_scopes": sorted(granted), "missing_scopes": sorted(set(scopes_for_role(role)) - granted),
        "capabilities": [{"key": key, "title": CAPABILITIES[key].title} for key in capabilities_for_role(role)],
    }}


class LoginCallback(BaseModel):
    state: str = Field(min_length=32, max_length=128)
    browser_secret: str = Field(min_length=32, max_length=128)
    code: str = Field(min_length=1, max_length=2048)


def _complete_login(request: LoginCallback) -> dict:
    tokens.validate_enterprise_config()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        flow = conn.execute(
            "SELECT * FROM feishu_oauth_flows WHERE state_hash = ?", (_digest(request.state),),
        ).fetchone()
        if not flow or flow["expires_at"] <= store.now() or not secrets.compare_digest(
            flow["browser_hash"], _digest(request.browser_secret),
        ):
            raise HTTPException(status_code=400, detail="登录请求已过期或不属于此浏览器，请重新登录。")
        conn.execute("DELETE FROM feishu_oauth_flows WHERE state_hash = ?", (_digest(request.state),))
    grant = tokens.request_token({
        "grant_type": "authorization_code", "code": request.code,
        "redirect_uri": get_settings().FEISHU_REDIRECT_URI,
    })
    user = tokens.verified_user(grant["access_token"])
    app_id = get_settings().FEISHU_APP_ID
    subject = (app_id, user["tenant_key"], user["open_id"])
    existing = store.query_one(
        "SELECT account FROM feishu_identities WHERE app_id = ? AND tenant_key = ? AND open_id = ?", subject,
    )
    account = flow["account"] or (existing["account"] if existing else "feishu-" + _digest("|".join(subject))[:32])
    with tokens.account_token_lock(account):
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            invitation = None
            if flow["invitation_id"]:
                invitation = conn.execute(
                    "SELECT * FROM account_invitations WHERE id = ?", (flow["invitation_id"],),
                ).fetchone()
                if not _invitation_active(invitation, store.now()):
                    raise HTTPException(status_code=410, detail="邀请已失效，请联系管理员重新邀请。")
            epoch = conn.execute("SELECT epoch FROM account_auth_epochs WHERE account = ?", (account,)).fetchone()
            if flow["account"] and flow["account_epoch"] != (epoch["epoch"] if epoch else 0):
                raise HTTPException(status_code=409, detail="连接状态已变更，请重新发起授权。")
            bound = conn.execute("SELECT * FROM feishu_identities WHERE account = ?", (account,)).fetchone()
            owner = conn.execute(
                "SELECT account FROM feishu_identities WHERE app_id = ? AND tenant_key = ? AND open_id = ?", subject,
            ).fetchone()
            if (owner and owner["account"] != account) or (bound and (
                bound["app_id"], bound["tenant_key"], bound["open_id"]
            ) != subject):
                raise HTTPException(status_code=409, detail="飞书身份已绑定其他账号，请先由管理员处理解绑。")
            row = conn.execute(
                """SELECT a.account, a.name, COALESCE(m.role, 'employee') AS role,
                          COALESCE(m.enabled, 1) AS enabled FROM accounts a
                   LEFT JOIN account_memberships m ON m.account = a.account WHERE a.account = ?""", (account,),
            ).fetchone()
            if row and not row["enabled"]:
                raise HTTPException(status_code=403, detail="账号已停用，请联系管理员。")
            if flow["account"] and not row:
                raise HTTPException(status_code=403, detail="原账号已不存在，请重新登录。")
            if not row:
                conn.execute(
                    "INSERT INTO accounts(account, name, password_hash, created_at, updated_at) VALUES (?, ?, '', ?, ?)",
                    (account, user["name"], store.now(), store.now()),
                )
                conn.execute(
                    "INSERT INTO account_memberships(account, role, updated_at) VALUES (?, 'employee', ?)",
                    (account, store.now()),
                )
            conn.execute(
                "INSERT OR IGNORE INTO feishu_identities(account, app_id, tenant_key, open_id) VALUES (?, ?, ?, ?)",
                (account, *subject),
            )
            tokens.save_grant(account, grant, conn=conn)
            if invitation is not None:
                now = store.now()
                updated = conn.execute(
                    """UPDATE account_invitations SET redeemed_at = ?, redeemed_by = ?
                       WHERE id = ? AND revoked_at IS NULL AND redeemed_at IS NULL AND expires_at > ?""",
                    (now, account, invitation["id"], now),
                )
                if updated.rowcount != 1:
                    raise HTTPException(status_code=410, detail="邀请已失效，请联系管理员重新邀请。")
                conn.execute(
                    """INSERT INTO account_audit(actor, subject, action, details_json, created_at)
                       VALUES (?, ?, 'member_invitation_redeemed', ?, ?)""",
                    (account, account, store.dumps({"invitation_id": invitation["id"]}), now),
                )
        return create_auth_session(account, row["name"] if row else user["name"], row["role"] if row else "employee")


@router.post("/auth/feishu/complete")
async def complete_login(request: LoginCallback) -> dict:
    try:
        return await asyncio.to_thread(_complete_login, request)
    except tokens.FeishuAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/auth/feishu/connection")
async def disconnect_self(account: AccountInfo = CURRENT_ACCOUNT_DEPENDENCY) -> dict:
    return {"code": 0, "data": await asyncio.to_thread(tokens.disconnect_account, account.account, account.account)}


@router.delete("/admin/members/{member}/feishu")
async def disconnect_member(member: str, admin: AccountInfo = ADMIN_ACCOUNT_DEPENDENCY) -> dict:
    if not store.query_one("SELECT account FROM accounts WHERE account = ?", (member,)):
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"code": 0, "data": await asyncio.to_thread(tokens.disconnect_account, member, admin.account)}


@router.get("/admin/invitations")
async def list_invitations(_admin: Annotated[AccountInfo, Depends(get_admin_account)]) -> dict:
    rows = store.query_all(
        """SELECT id, created_by, created_at, expires_at, revoked_at, redeemed_at, redeemed_by
           FROM account_invitations ORDER BY created_at DESC, id DESC"""
    )
    now = store.now()
    invitations = []
    for row in rows:
        if row["redeemed_at"] is not None:
            status = "redeemed"
        elif row["revoked_at"] is not None:
            status = "revoked"
        elif row["expires_at"] <= now:
            status = "expired"
        else:
            status = "pending"
        invitations.append({**dict(row), "status": status})
    return {"code": 0, "data": invitations}


@router.post("/admin/invitations", status_code=201)
async def create_invitation(response: Response, admin: Annotated[AccountInfo, Depends(get_admin_account)]) -> dict:
    try:
        tokens.validate_enterprise_config()
    except tokens.FeishuAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    invitation_id = secrets.token_urlsafe(16)
    invitation_token = secrets.token_urlsafe(32)
    now = store.now()
    expires_at = now + INVITATION_TTL_SECONDS
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_active_admin(conn, admin.account)
        conn.execute(
            """INSERT INTO account_invitations(id, token_hash, created_by, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (invitation_id, _digest(invitation_token), admin.account, now, expires_at),
        )
        conn.execute(
            """INSERT INTO account_audit(actor, subject, action, details_json, created_at)
               VALUES (?, ?, 'member_invitation_created', ?, ?)""",
            (admin.account, invitation_id, store.dumps({"expires_at": expires_at}), now),
        )
    response.headers["Cache-Control"] = "no-store"
    url = f"{get_settings().FEISHU_REDIRECT_URI}#{urlencode({'invite': invitation_token})}"
    return {"code": 0, "data": {"id": invitation_id, "url": url, "expires_at": expires_at}}


@router.delete("/admin/invitations/{invitation_id}")
async def revoke_invitation(invitation_id: str, admin: Annotated[AccountInfo, Depends(get_admin_account)]) -> dict:
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_active_admin(conn, admin.account)
        invitation = conn.execute(
            "SELECT * FROM account_invitations WHERE id = ?", (invitation_id,),
        ).fetchone()
        if not invitation:
            raise HTTPException(status_code=404, detail="邀请不存在。")
        if invitation["redeemed_at"] is not None:
            raise HTTPException(status_code=409, detail="邀请已使用，无法撤销。")
        if invitation["revoked_at"] is None:
            now = store.now()
            conn.execute("UPDATE account_invitations SET revoked_at = ? WHERE id = ?", (now, invitation_id))
            conn.execute("DELETE FROM feishu_oauth_flows WHERE invitation_id = ?", (invitation_id,))
            conn.execute(
                """INSERT INTO account_audit(actor, subject, action, details_json, created_at)
                   VALUES (?, ?, 'member_invitation_revoked', '{}', ?)""",
                (admin.account, invitation_id, now),
            )
    return {"code": 0, "data": {"revoked": True}}
