from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import AccountInfo, create_auth_session, get_current_account_optional, get_current_account, get_admin_account
from app.config import get_settings
from app.core import feishu_tokens as tokens
from app.core.storage import store
from app.core.feishu_permissions import CAPABILITIES, capabilities_for_role, member_role, scopes_for_role

router = APIRouter()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@router.get("/auth/feishu/config")
async def login_config() -> dict:
    return {"code": 0, "data": {"enabled": tokens.enterprise_configured()}}


class AuthorizationRequest(BaseModel):
    scopes: list[str] = Field(default_factory=list, max_length=200)


@router.post("/auth/feishu/start")
async def start_login(
    request: AuthorizationRequest = AuthorizationRequest(),
    account: AccountInfo | None = Depends(get_current_account_optional),
) -> dict:
    try:
        tokens.validate_enterprise_config()
    except tokens.FeishuAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    allowed = set(scopes_for_role(member_role(account.account) if account else "employee"))
    # A new device first proves identity; business permissions belong to the account.
    requested = set(request.scopes) if request.scopes else (allowed if account else {"offline_access"})
    if requested - allowed:
        raise HTTPException(status_code=403, detail="请求的权限不属于当前角色的企业能力清单")
    requested.add("offline_access")
    if account:
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
    state, browser_secret, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    store.execute("DELETE FROM feishu_oauth_flows WHERE expires_at <= ?", (store.now(),))
    epoch = store.query_one("SELECT epoch FROM account_auth_epochs WHERE account = ?", (account.account,)) if account else None
    store.execute(
        "INSERT INTO feishu_oauth_flows(state_hash, browser_hash, verifier, account, expires_at, account_epoch) VALUES (?, ?, ?, ?, ?, ?)",
        (_digest(state), _digest(browser_secret), verifier, account.account if account else None, store.now() + 300, epoch["epoch"] if epoch else 0),
    )
    url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode({
        "client_id": get_settings().FEISHU_APP_ID, "response_type": "code",
        "redirect_uri": get_settings().FEISHU_REDIRECT_URI,
        "scope": " ".join(sorted(requested)), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    return {"code": 0, "data": {"url": url, "state": state, "browser_secret": browser_secret}}


@router.get("/auth/feishu/status")
async def authorization_status(account: AccountInfo = Depends(get_current_account)) -> dict:
    role = member_role(account.account)
    row = store.query_one("SELECT scopes, expires_at, refresh_expires_at, revoked FROM feishu_grants WHERE account = ?", (account.account,))
    granted = set(row["scopes"].split()) if row and not row["revoked"] else set()
    return {"code": 0, "data": {
        "enabled": tokens.enterprise_configured(), "role": role,
        "connected": bool(row and not row["revoked"] and max(row["expires_at"], row["refresh_expires_at"]) > store.now()),
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
        "redirect_uri": get_settings().FEISHU_REDIRECT_URI, "code_verifier": flow["verifier"],
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
        tokens.save_grant(account, grant)
        return create_auth_session(account, row["name"] if row else user["name"], row["role"] if row else "employee")


@router.post("/auth/feishu/complete")
async def complete_login(request: LoginCallback) -> dict:
    try:
        return await asyncio.to_thread(_complete_login, request)
    except tokens.FeishuAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/auth/feishu/connection")
async def disconnect_self(account: AccountInfo = Depends(get_current_account)) -> dict:
    return {"code": 0, "data": await asyncio.to_thread(tokens.disconnect_account, account.account, account.account)}


@router.delete("/admin/members/{member}/feishu")
async def disconnect_member(member: str, admin: AccountInfo = Depends(get_admin_account)) -> dict:
    if not store.query_one("SELECT account FROM accounts WHERE account = ?", (member,)):
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"code": 0, "data": await asyncio.to_thread(tokens.disconnect_account, member, admin.account)}
