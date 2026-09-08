from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings
from app.core.account_access import account_execution_error
from app.core.storage import store

TOKEN_URL = "https://accounts.feishu.cn/oauth/v3/token"
AUTHORIZATION_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
logger = logging.getLogger(__name__)


class FeishuAuthError(Exception):
    def __init__(self, message: str, *, reauthorize: bool = False):
        super().__init__(message)
        self.reauthorize = reauthorize


def enterprise_configured() -> bool:
    settings = get_settings()
    return all((settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET,
                settings.FEISHU_TENANT_KEY, settings.FEISHU_REDIRECT_URI,
                settings.FEISHU_TOKEN_ENCRYPTION_KEY))


def validate_enterprise_config() -> None:
    if not enterprise_configured():
        raise FeishuAuthError("企业飞书登录尚未配置完成，请联系管理员。")
    target = urlparse(get_settings().FEISHU_REDIRECT_URI)
    if target.fragment or target.query or target.username or not target.netloc:
        raise FeishuAuthError("飞书回调地址配置无效。")
    if target.scheme != "https" and not (
        target.scheme == "http" and target.hostname in {"localhost", "127.0.0.1"}
    ):
        raise FeishuAuthError("飞书回调地址必须使用 HTTPS。")
    _cipher()


def _cipher() -> AESGCM:
    try:
        key = base64.b64decode(get_settings().FEISHU_TOKEN_ENCRYPTION_KEY, altchars=b"-_", validate=True)
        if len(key) != 32:
            raise ValueError("invalid length")
        return AESGCM(key)
    except (ValueError, TypeError) as exc:
        raise FeishuAuthError("服务端令牌加密密钥配置无效。") from exc


def encrypt_token(account: str, payload: dict[str, Any]) -> str:
    nonce = os.urandom(12)
    encrypted = _cipher().encrypt(nonce, json.dumps(payload).encode(), account.encode())
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def decrypt_token(account: str, value: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(value, altchars=b"-_", validate=True)
        payload = json.loads(_cipher().decrypt(raw[:12], raw[12:], account.encode()))
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        return payload
    except (ValueError, InvalidTag) as exc:
        raise FeishuAuthError("已保存的飞书授权无法解密，请联系管理员。") from exc


@contextmanager
def account_token_lock(account: str) -> Iterator[None]:
    # Refresh tokens are single-use. A file lock also serializes separate workers.
    directory = store.db_path.parent / "token_locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(account.encode()).hexdigest() + ".lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.monotonic() + 25
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise FeishuAuthError("飞书授权正在更新，请稍后重试。")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def request_token(fields: dict[str, str]) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            body = {
                "client_id": settings.FEISHU_APP_ID,
                "client_secret": settings.FEISHU_APP_SECRET,
                **fields,
            }
            # The authorize endpoint currently documents PKCE compatibility with v2.
            if fields.get("grant_type") == "authorization_code":
                response = client.post(AUTHORIZATION_TOKEN_URL, json=body)
            else:
                response = client.post(TOKEN_URL, data=body)
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise FeishuAuthError("暂时无法连接飞书认证服务，请稍后重试。") from exc
    if not isinstance(payload, dict):
        raise FeishuAuthError("飞书认证服务返回了无效响应。")
    if response.is_error or payload.get("code", 0) != 0 or payload.get("error"):
        logger.warning("Feishu OAuth failed: HTTP %s code=%s", response.status_code,
                       payload.get("code") if isinstance(payload.get("code"), int) else "unknown")
        reauthorize = payload.get("code") in {20008, 20010, 20026, 20037, 20064, 20066, 20073}
        raise FeishuAuthError(
            "飞书授权已失效，请重新连接。" if reauthorize else "飞书认证未成功，请检查企业应用配置后重试。",
            reauthorize=reauthorize,
        )
    if not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
        raise FeishuAuthError("飞书认证响应缺少访问凭据。")
    return payload


def verified_user(access_token: str) -> dict[str, str]:
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.get(USER_INFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise FeishuAuthError("暂时无法验证飞书身份，请重新登录。") from exc
    if not isinstance(payload, dict) or response.is_error or payload.get("code") != 0:
        raise FeishuAuthError("飞书身份验证未成功，请重新登录。")
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("open_id") or not data.get("tenant_key"):
        raise FeishuAuthError("飞书身份缺少企业或用户标识。")
    if data["tenant_key"] != get_settings().FEISHU_TENANT_KEY:
        raise FeishuAuthError("当前飞书账号不属于本企业。")
    return {"open_id": str(data["open_id"]), "tenant_key": str(data["tenant_key"]),
            "name": str(data.get("name") or "飞书用户")}


def save_grant(account: str, payload: dict[str, Any]) -> None:
    now = store.now()
    try:
        lifetime = int(payload["expires_in"])
        refresh_lifetime = int(payload.get("refresh_token_expires_in", 0))
        if lifetime <= 0 or refresh_lifetime < 0:
            raise ValueError("invalid lifetime")
    except (KeyError, ValueError, TypeError) as exc:
        raise FeishuAuthError("飞书认证响应缺少有效的过期时间。") from exc
    store.execute(
        """INSERT INTO feishu_grants(account, encrypted_token, scopes, expires_at,
                   refresh_expires_at, revoked, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?)
           ON CONFLICT(account) DO UPDATE SET encrypted_token=excluded.encrypted_token,
               scopes=excluded.scopes, expires_at=excluded.expires_at,
               refresh_expires_at=excluded.refresh_expires_at, revoked=0, updated_at=excluded.updated_at""",
        (account, encrypt_token(account, payload), str(payload.get("scope") or ""),
         now + lifetime, now + refresh_lifetime if payload.get("refresh_token") else 0, now),
    )


def access_token_for_account(account: str) -> str:
    validate_enterprise_config()
    with account_token_lock(account):
        error = account_execution_error(account)
        if error:
            raise FeishuAuthError(error)
        identity = store.query_one("SELECT * FROM feishu_identities WHERE account = ?", (account,))
        settings = get_settings()
        if not identity or identity["app_id"] != settings.FEISHU_APP_ID or identity["tenant_key"] != settings.FEISHU_TENANT_KEY:
            raise FeishuAuthError("请先连接当前企业的飞书账号。", reauthorize=True)
        row = store.query_one("SELECT * FROM feishu_grants WHERE account = ?", (account,))
        if not row or row["revoked"]:
            raise FeishuAuthError("请重新连接飞书账号。", reauthorize=True)
        payload = decrypt_token(account, row["encrypted_token"])
        now = store.now()
        if row["expires_at"] > now + 60:
            return str(payload["access_token"])
        if not payload.get("refresh_token") or row["refresh_expires_at"] <= now:
            if row["expires_at"] > now:
                return str(payload["access_token"])
            raise FeishuAuthError("飞书授权已到期，请重新连接。", reauthorize=True)
        try:
            refreshed = request_token({"grant_type": "refresh_token", "refresh_token": payload["refresh_token"]})
        except FeishuAuthError as exc:
            if exc.reauthorize:
                store.execute("UPDATE feishu_grants SET revoked = 1 WHERE account = ?", (account,))
            raise
        save_grant(account, refreshed)
        # Membership can change while the remote request is in flight.
        error = account_execution_error(account)
        if error:
            raise FeishuAuthError(error)
        return str(refreshed["access_token"])


def disconnect_account(account: str, actor: str) -> dict[str, Any]:
    with account_token_lock(account):
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM feishu_grants WHERE account = ?", (account,)).fetchone()
            conn.execute("UPDATE feishu_grants SET revoked = 1 WHERE account = ?", (account,))
            conn.execute("DELETE FROM feishu_oauth_flows WHERE account = ?", (account,))
            conn.execute(
                """INSERT INTO account_auth_epochs(account, epoch) VALUES (?, 1)
                   ON CONFLICT(account) DO UPDATE SET epoch=epoch+1""", (account,),
            )
            conn.execute(
                "INSERT INTO account_audit(actor, subject, action, details_json, created_at) VALUES (?, ?, 'feishu_disconnected', '{}', ?)",
                (actor, account, store.now()),
            )
        if not row:
            return {"disconnected": True, "remote_revoked": True}
        try:
            payload = decrypt_token(account, row["encrypted_token"])
            settings = get_settings()
            identity = store.query_one("SELECT app_id FROM feishu_identities WHERE account = ?", (account,))
            if not identity or identity["app_id"] != settings.FEISHU_APP_ID:
                raise FeishuAuthError("企业应用已变更，无法撤销旧应用授权。")
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                for key in ("refresh_token", "access_token"):
                    if not payload.get(key):
                        continue
                    response = client.post("https://accounts.feishu.cn/oauth/v1/revoke", data={
                        "client_id": settings.FEISHU_APP_ID, "client_secret": settings.FEISHU_APP_SECRET,
                        "token": payload[key], "token_type_hint": key,
                    })
                    result = response.json() if response.content else {}
                    if response.is_error or not isinstance(result, dict) or result.get("code", 0) != 0 or result.get("error"):
                        raise FeishuAuthError("飞书端撤销授权暂未成功。")
        except (httpx.HTTPError, ValueError, FeishuAuthError):
            return {"disconnected": True, "remote_revoked": False,
                    "message": "网站已停止使用此授权，飞书端撤销暂未成功，可再次重试断开。"}
        store.execute("DELETE FROM feishu_grants WHERE account = ?", (account,))
        return {"disconnected": True, "remote_revoked": True}
