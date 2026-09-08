"""Text-only transports; wire contract checked against Weixin plugin 2.4.6."""

from __future__ import annotations

import base64
import logging
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

import httpx

WEIXIN_BASE = "https://ilinkai.weixin.qq.com"


class RedactBotURLs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(r"/bot[0-9]+:[^/\s\"]+", "/bot[redacted]", record.getMessage())
        record.msg = re.sub(r"(qrcode|verify_code)=[^&\s\"]+", r"\1=[redacted]", record.msg)
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(RedactBotURLs())


def weixin_base(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or not (parsed.hostname == "ilinkai.weixin.qq.com" or parsed.hostname.endswith(".ilinkai.weixin.qq.com"))
    ):
        raise ValueError("微信接口返回了未允许的服务地址")
    return f"https://{parsed.hostname}"


async def weixin(
    method: str,
    endpoint: str,
    *,
    base: str = WEIXIN_BASE,
    token: str = "",
    payload: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 20,
) -> dict[str, Any]:
    headers = {
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": str((2 << 16) + (4 << 8) + 6),
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": base64.b64encode(str(secrets.randbits(32)).encode()).decode(),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {**(payload or {}), "base_info": {"channel_version": "2.4.6", "bot_agent": "FeishuCLIWeb/0.1.0"}}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.request(
            method,
            f"{weixin_base(base)}/{endpoint}",
            headers=headers,
            params=params,
            **({"json": body} if method == "POST" else {}),
        )
        if response.status_code != 200:
            raise ValueError(f"微信连接失败（HTTP {response.status_code}），请稍后重试")
        data = response.json()
    if not isinstance(data, dict) or data.get("ret", 0) != 0 or data.get("errcode", 0) != 0:
        raise ValueError("微信连接失效或接口拒绝请求，请检查连接并重新扫码")
    return data


async def telegram(token: str, method: str, payload: dict[str, Any] | None = None) -> Any:
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("Telegram Bot Token 配置无效")
    async with httpx.AsyncClient(timeout=40, follow_redirects=False) as client:
        response = await client.post(f"https://api.telegram.org/bot{token}/{method}", json=payload or {})
        if response.status_code != 200:
            raise ValueError(f"Telegram 连接失败（HTTP {response.status_code}）")
        data = response.json()
    if not isinstance(data, dict) or not data.get("ok"):
        raise ValueError("Telegram 接口拒绝请求，请检查机器人配置")
    return data["result"]


def weixin_text(message: dict[str, Any], peer_id: str) -> str | None:
    if (
        message.get("from_user_id") != peer_id
        or message.get("group_id")
        or message.get("message_type") != 1
        or message.get("message_state") != 2
    ):
        return None
    items = message.get("item_list") or []
    if not isinstance(items, list) or any(not isinstance(item, dict) or item.get("type") != 1 for item in items):
        return "[不支持的消息类型：请发送纯文字指令]"
    return "\n".join(str(item.get("text_item", {}).get("text") or "") for item in items)


async def send_reply(
    binding: dict[str, Any], credentials: dict[str, Any], text: str, context: str, delivery_id: str, telegram_token: str
) -> None:
    # Bound output size; full answer remains in the employee's website history.
    if len(text) > 1800:
        text = text[:1700] + "\n\n回复较长，完整内容请登录网站查看此机器人会话。"
    if binding["channel"] == "telegram":
        await telegram(
            telegram_token,
            "sendMessage",
            {"chat_id": binding["peer_id"], "text": text, "link_preview_options": {"is_disabled": True}},
        )
    else:
        if not context:
            raise ValueError("微信会话上下文缺失，不能发送回复")
        await weixin(
            "POST",
            "ilink/bot/sendmessage",
            base=credentials["base"],
            token=credentials["token"],
            payload={
                "msg": {
                    "from_user_id": "",
                    "to_user_id": binding["peer_id"],
                    "client_id": delivery_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                }
            },
        )
