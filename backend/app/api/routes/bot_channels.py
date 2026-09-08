from __future__ import annotations

import base64
import io
import sqlite3
from typing import Annotated, Literal

import httpx
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.routes.auth import AccountInfo, get_current_account
from app.config import get_settings
from app.core.bot_channels import channel_store
from app.core.bot_protocols import WEIXIN_BASE, telegram, weixin, weixin_base
from app.core.feishu_tokens import FeishuAuthError, encrypt_token

router = APIRouter(prefix="/bot-channels")
CurrentAccount = Annotated[AccountInfo, Depends(get_current_account)]


def available() -> None:
    if not get_settings().BOT_CHANNELS_ENABLED:
        raise HTTPException(503, "机器人渠道未启用，请联系部署管理员")
    try:
        encrypt_token("bot-config-check", {})
    except FeishuAuthError as exc:
        raise HTTPException(503, "请先配置服务端令牌加密密钥，再启用机器人绑定") from exc


def qr_image(content: str) -> str:
    if not content or len(content) > 2000:
        raise ValueError("二维码内容无效")
    image = qrcode.make(content, image_factory=qrcode.image.svg.SvgPathImage)
    output = io.BytesIO()
    image.save(output)
    return "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode()


@router.get("")
async def status(response: Response, account: CurrentAccount):
    response.headers["Cache-Control"] = "no-store"
    settings = get_settings()
    return {
        "code": 0,
        "data": {
            "enabled": settings.BOT_CHANNELS_ENABLED,
            "worker_running": channel_store.db.now() - int(channel_store.cursor("worker-heartbeat") or 0) < 15,
            "telegram_configured": bool(settings.TELEGRAM_BOT_TOKEN),
            "bindings": channel_store.list_owned(account.account),
        },
    }


@router.post("/{channel}/pair")
async def start(channel: Literal["weixin", "telegram"], response: Response, account: CurrentAccount):
    available()
    response.headers["Cache-Control"] = "no-store"
    db = channel_store.db
    existing = db.query_one(
        "SELECT id FROM bot_pairings WHERE account=? AND channel=? AND expires_at>?",
        (account.account, channel, db.now()),
    )
    if existing:
        p = channel_store.pairing(account.account, existing["id"])
        return {
            "code": 0,
            "data": {"id": p["id"], "image": qr_image(p["payload"]["qr_url"]), "expires_at": p["expires_at"]},
        }
    try:
        if channel == "weixin":
            result = await weixin(
                "POST", "ilink/bot/get_bot_qrcode", params={"bot_type": "3"}, payload={"local_token_list": []}
            )
            payload = {"qrcode": result["qrcode"], "qr_url": result["qrcode_img_content"], "base": WEIXIN_BASE}
            pairing_id, _ = channel_store.new_pairing(account.account, channel, payload)
        else:
            token = get_settings().TELEGRAM_BOT_TOKEN
            bot = await telegram(token, "getMe")
            payload = {"bot_id": str(bot["id"]), "qr_url": "pending"}
            pairing_id, nonce = channel_store.new_pairing(account.account, channel, payload)
            payload["qr_url"] = f"https://t.me/{bot['username']}?start={nonce}"
            p = channel_store.pairing(account.account, pairing_id)
            channel_store.update_pairing(p, payload, "waiting")
        return {
            "code": 0,
            "data": {"id": pairing_id, "image": qr_image(payload["qr_url"]), "expires_at": db.now() + 300},
        }
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(502, "机器人连接暂不可用，请检查网络、渠道配置或稍后重试") from exc


class PollRequest(BaseModel):
    verify_code: str = Field(default="", pattern=r"^[0-9]{0,12}$")


@router.post("/pairings/{pairing_id}/poll")
async def poll(pairing_id: str, request: PollRequest, response: Response, account: CurrentAccount):
    available()
    response.headers["Cache-Control"] = "no-store"
    p = channel_store.pairing(account.account, pairing_id)
    if not p:
        raise HTTPException(404, "二维码已过期或不属于当前账号，请重新生成")
    state = p["status"]
    if p["channel"] == "weixin" and state != "scanned":
        payload = p["payload"]
        params = {"qrcode": payload["qrcode"]}
        if request.verify_code:
            params["verify_code"] = request.verify_code
        try:
            result = await weixin("GET", "ilink/bot/get_qrcode_status", base=payload["base"], params=params, timeout=35)
            state = result.get("status", "wait")
            if state == "confirmed":
                if not all(
                    isinstance(result.get(key), str) and result[key]
                    for key in ("bot_token", "ilink_bot_id", "ilink_user_id")
                ):
                    raise ValueError("微信未返回完整身份信息")
                payload.update(
                    token=result["bot_token"],
                    bot_id=result["ilink_bot_id"],
                    peer_id=result["ilink_user_id"],
                    base=weixin_base(result.get("baseurl") or WEIXIN_BASE),
                )
                state = "scanned"
            elif state == "scaned_but_redirect":
                payload["base"] = weixin_base("https://" + str(result.get("redirect_host") or ""))
            channel_store.update_pairing(p, payload, state)
        except httpx.TimeoutException:
            state = "wait"
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "微信扫码状态获取失败，请重试；未建立绑定") from exc
    latest = channel_store.pairing(account.account, pairing_id)
    if latest and latest["status"] == "scanned":
        state = "scanned"
    return {"code": 0, "data": {"status": state, "peer_id": (latest or p)["payload"].get("peer_id", "")}}


@router.post("/pairings/{pairing_id}/confirm")
async def confirm(pairing_id: str, account: CurrentAccount):
    available()
    try:
        binding_id = channel_store.confirm(account.account, pairing_id)
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, "绑定已失效或账号已绑定，请检查原绑定后重试") from exc
    return {"code": 0, "data": {"id": binding_id}}


@router.delete("/pairings/{pairing_id}")
async def cancel_pairing(pairing_id: str, account: CurrentAccount):
    channel_store.db.execute("DELETE FROM bot_pairings WHERE id=? AND account=?", (pairing_id, account.account))
    return {"code": 0, "data": {}}


@router.delete("/bindings/{binding_id}")
async def unbind(binding_id: str, account: CurrentAccount):
    channel_store.unbind(account.account, binding_id)
    return {"code": 0, "data": {"message": "已解绑，历史会话仍仅本人可见。已经提交到飞书的操作不会撤回。"}}
