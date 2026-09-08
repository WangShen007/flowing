"""Single-host durable polling runner. File lock elects one runner across ASGI workers."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.core.bot_channels import channel_execution, channel_store
from app.core.bot_protocols import send_reply, telegram, weixin, weixin_text
from app.core.feishu_tokens import decrypt_token

logger = logging.getLogger(__name__)


async def process_message(binding: dict[str, Any], text: str) -> str:
    from app.api.routes.auth import AccountInfo
    from app.api.routes.chat import ChatRequest, chat

    if not channel_store.active(binding["id"], binding["account"]):
        raise ValueError("渠道已解绑或账号已停用")
    if text == "[不支持的消息类型：请发送纯文字指令]":
        return "目前仅支持纯文字指令，图片、语音和文件请在网站处理。"
    resume_id = ""
    if text.startswith("/confirm "):
        resume_id = channel_store.take_approval(binding["id"], text.removeprefix("/confirm ").strip())
    elif text.startswith("/"):
        return (
            "直接发送文字即可使用飞书助手。写操作需要核对执行内容，再发送对应的 /confirm 确认码；账号授权请在网站完成。"
        )
    context_token = channel_execution.set(binding["id"])
    try:
        response = await chat(
            ChatRequest(
                message=text,
                session_id=binding["session_id"],
                skill="lark_cli",
                stream=False,
                resume_id=resume_id,
                confirm_write=bool(resume_id),
            ),
            AccountInfo(account=binding["account"], name=binding["name"], role=binding["role"]),
        )
    finally:
        channel_execution.reset(context_token)
    data = response["data"]
    metadata = data.get("metadata") or {}
    content = str(data.get("content") or "处理完成，请在网站查看详情。")
    if not channel_store.active(binding["id"], binding["account"]):
        channel_store.db.execute(
            "DELETE FROM workflow_checkpoints WHERE user_id=? AND session_id=?",
            (binding["account"], binding["session_id"]),
        )
        raise ValueError("渠道已解绑或账号已停用")
    if metadata.get("approval_required") and metadata.get("resume_id"):
        # Never send a confirmation code beside a truncated approval description.
        if len(content) <= 1200:
            code = channel_store.approval(binding["id"], metadata["resume_id"])
            content += f"\n\n核对以上操作后，10 分钟内发送：\n/confirm {code}\n仅此账号和对话可用，使用一次即失效。"
        else:
            content = "任务需要确认，执行内容较长。请登录网站，在机器人会话中核对完整操作后确认；本次没有批准写入。"
    if metadata.get("setup_required"):
        content += "\n请登录网站，用自己的账号完成飞书授权后重试。"
    return content


class BotRunner:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.pollers: dict[str, asyncio.Task] = {}
        self.jobs: dict[str, asyncio.Task] = {}

    def start(self) -> None:
        if get_settings().BOT_CHANNELS_ENABLED and (not self.task or self.task.done()):
            self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self) -> None:
        db = channel_store.db
        with db.db_path.with_suffix(".bots.lock").open("a") as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(3)
            # A process died after claiming: execution/delivery might already have happened.
            # Do not automatically replay either unknown Feishu work or unknown sends.
            db.execute("UPDATE bot_inbox SET status='execution_unknown' WHERE status='running'")
            db.execute("UPDATE bot_inbox SET status='delivery_unknown' WHERE status='sending'")
            try:
                while True:
                    db.execute(
                        "INSERT INTO bot_cursors VALUES ('worker-heartbeat',?) "
                        "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
                        (str(db.now()),),
                    )
                    active = {
                        row["id"]: dict(row)
                        for row in db.query_all("SELECT id FROM bot_bindings")
                        if channel_store.active(row["id"])
                    }
                    for key, task in list(self.jobs.items()):
                        if key not in active:
                            task.cancel()
                        if task.done():
                            await asyncio.gather(task, return_exceptions=True)
                            self.jobs.pop(key, None)
                    for key, task in list(self.pollers.items()):
                        if key != "telegram" and key not in active:
                            task.cancel()
                        if task.done():
                            await asyncio.gather(task, return_exceptions=True)
                            self.pollers.pop(key, None)
                    for key in active:
                        binding = channel_store.active(key)
                        if binding["channel"] == "weixin" and key not in self.pollers:
                            self.pollers[key] = asyncio.create_task(self.poll_weixin(key))
                    if get_settings().TELEGRAM_BOT_TOKEN and "telegram" not in self.pollers:
                        self.pollers["telegram"] = asyncio.create_task(self.poll_telegram())
                    busy_accounts = {
                        channel_store.active(key)["account"] for key in self.jobs if channel_store.active(key)
                    }
                    for row in db.query_all("SELECT * FROM bot_inbox WHERE status IN ('queued','ready') ORDER BY id"):
                        if len(self.jobs) >= 4:
                            break
                        binding = channel_store.active(row["binding_id"])
                        if not binding or binding["id"] in self.jobs or binding["account"] in busy_accounts:
                            continue
                        busy_accounts.add(binding["account"])
                        self.jobs[binding["id"]] = asyncio.create_task(self.handle(binding, dict(row)))
                    # Remove expired binding challenges/secrets; retain only a bounded dedup window.
                    db.execute("DELETE FROM bot_pairings WHERE expires_at<=?", (db.now(),))
                    db.execute("DELETE FROM bot_approvals WHERE expires_at<=?", (db.now(),))
                    db.execute(
                        "DELETE FROM bot_inbox WHERE created_at<? AND status NOT IN ('queued','running','ready','sending')",
                        (db.now() - 7 * 86400,),
                    )
                    await asyncio.sleep(1)
            finally:
                tasks = [*self.pollers.values(), *self.jobs.values()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.pollers.clear()
                self.jobs.clear()

    async def handle(self, binding: dict[str, Any], row: dict[str, Any]) -> None:
        db = channel_store.db
        try:
            if row["status"] == "queued":
                db.execute("UPDATE bot_inbox SET status='running' WHERE id=?", (row["id"],))
                try:
                    reply = await process_message(binding, row["text"])
                except Exception:  # noqa: BLE001 - execution boundary; never log credential-bearing exceptions
                    logger.warning("Bot execution failed; event=%s", row["id"])
                    reply = "本次处理未正常完成。请登录网站核对执行详情；已提交的操作可能已生效，请勿直接重复执行。"
                db.execute("UPDATE bot_inbox SET status='ready',reply=? WHERE id=?", (reply, row["id"]))
                row["reply"] = reply
            if not channel_store.active(binding["id"], binding["account"]):
                return
            context = decrypt_token(f"bot-context:{binding['id']}", row["context"])["token"]
            db.execute("UPDATE bot_inbox SET status='sending' WHERE id=?", (row["id"],))
            await send_reply(
                binding,
                channel_store.credentials(binding),
                row["reply"],
                context,
                f"feishu-web-{row['id']}",
                get_settings().TELEGRAM_BOT_TOKEN,
            )
            db.execute("UPDATE bot_inbox SET status='sent' WHERE id=?", (row["id"],))
        except asyncio.CancelledError:
            db.execute(
                "UPDATE bot_inbox SET status=CASE WHEN status='sending' THEN 'delivery_unknown' ELSE 'execution_unknown' END WHERE id=?",
                (row["id"],),
            )
            raise
        except Exception:  # noqa: BLE001 - persist uncertain delivery instead of replaying execution
            db.execute("UPDATE bot_inbox SET status='delivery_unknown' WHERE id=?", (row["id"],))
            logger.warning("Bot reply not confirmed; event=%s", row["id"])

    async def poll_weixin(self, binding_id: str) -> None:
        failures = 0
        while binding := channel_store.active(binding_id):
            try:
                credentials = channel_store.credentials(binding)
                response = await weixin(
                    "POST",
                    "ilink/bot/getupdates",
                    base=credentials["base"],
                    token=credentials["token"],
                    payload={"get_updates_buf": channel_store.cursor(binding_id)},
                    timeout=40,
                )
                for message in response.get("msgs", []):
                    if not isinstance(message, dict):
                        continue
                    text = weixin_text(message, binding["peer_id"])
                    if not text or int(message.get("create_time_ms") or 0) < binding["created_at"] * 1000:
                        continue
                    event_id = str(message.get("message_id") or message.get("client_id") or "")
                    channel_store.enqueue(binding_id, event_id, text, str(message.get("context_token") or ""))
                if "get_updates_buf" in response and channel_store.active(binding_id):
                    channel_store.set_cursor(binding_id, str(response["get_updates_buf"]))
                channel_store.health(binding_id, "ready")
                failures = 0
                await asyncio.sleep(0.2)
            except httpx.TimeoutException:
                await asyncio.sleep(1)
                continue
            except Exception:  # noqa: BLE001 - isolated provider boundary, retry reads with bounded backoff
                failures += 1
                channel_store.health(binding_id, "unavailable")
                logger.warning("Weixin polling unavailable; binding=%s", binding_id)
                await asyncio.sleep(min(60, 2 ** min(failures, 6)))

    async def poll_telegram(self) -> None:
        token = get_settings().TELEGRAM_BOT_TOKEN
        failures = 0
        while True:
            try:
                bot = await telegram(token, "getMe")
                bot_id = str(bot["id"])
                key = "telegram:" + bot_id + ":" + hashlib.sha256(token.encode()).hexdigest()[:16]
                while True:
                    result = await telegram(
                        token,
                        "getUpdates",
                        {"offset": int(channel_store.cursor(key) or 0), "timeout": 30, "allowed_updates": ["message"]},
                    )
                    for b in channel_store.db.query_all(
                        "SELECT id FROM bot_bindings WHERE channel='telegram' AND bot_id=?", (bot_id,)
                    ):
                        channel_store.health(b["id"], "ready")
                    for update in result:
                        message = update.get("message") or {}
                        chat = message.get("chat") or {}
                        sender = message.get("from") or {}
                        if (
                            chat.get("type") == "private"
                            and sender.get("id") == chat.get("id")
                            and not sender.get("is_bot")
                        ):
                            peer = str(sender["id"])
                            text = message.get("text") or "[不支持的消息类型：请发送纯文字指令]"
                            if text.startswith("/start "):
                                channel_store.telegram_candidate(text[7:].strip(), bot_id, peer)
                            else:
                                row = channel_store.db.query_one(
                                    "SELECT id,created_at FROM bot_bindings WHERE channel='telegram' AND bot_id=? AND peer_id=?",
                                    (bot_id, peer),
                                )
                                if row and int(message.get("date") or 0) >= row["created_at"]:
                                    channel_store.enqueue(row["id"], str(update["update_id"]), text)
                        channel_store.set_cursor(key, str(int(update["update_id"]) + 1))
                    failures = 0
                    await asyncio.sleep(0.2)
            except httpx.TimeoutException:
                await asyncio.sleep(1)
                continue
            except Exception:  # noqa: BLE001 - isolated provider boundary, retry reads with bounded backoff
                failures += 1
                for b in channel_store.db.query_all("SELECT id FROM bot_bindings WHERE channel='telegram'"):
                    channel_store.health(b["id"], "unavailable")
                logger.warning("Telegram polling unavailable; check network/token/webhook configuration")
                await asyncio.sleep(min(60, 2 ** min(failures, 6)))


bot_runner = BotRunner()
