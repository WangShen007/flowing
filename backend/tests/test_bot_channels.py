import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import bot_channels as routes
from app.api.routes.auth import AccountInfo, get_current_account
from app.core import bot_channels, bot_protocols, bot_runner, feishu_tokens
from app.core.storage import SQLiteStore


@pytest.fixture
def channels(tmp_path, monkeypatch):
    db = SQLiteStore(tmp_path / "bots.db")
    for account in ("alice", "bob", "admin"):
        db.execute("INSERT INTO accounts VALUES (?,?,?,1,1)", (account, account, ""))
        db.execute(
            "INSERT INTO account_memberships VALUES (?,?,1,1)", (account, "admin" if account == "admin" else "employee")
        )
    monkeypatch.setattr(
        feishu_tokens,
        "get_settings",
        lambda: SimpleNamespace(FEISHU_TOKEN_ENCRYPTION_KEY=base64.b64encode(b"x" * 32).decode()),
    )
    st = bot_channels.ChannelStore(db)
    for module in (bot_channels, routes, bot_runner):
        monkeypatch.setattr(module, "channel_store", st)
    return st


def bind(st, account="alice", channel="weixin", bot_id="bot1", peer="peer1"):
    pairing_id, _ = st.new_pairing(
        account,
        channel,
        {"bot_id": bot_id, "peer_id": peer, "token": "private-secret", "base": bot_protocols.WEIXIN_BASE},
    )
    p = st.pairing(account, pairing_id)
    st.update_pairing(p, p["payload"], "scanned")
    return st.confirm(account, pairing_id)


def test_binding_owned_encrypted_and_cannot_be_stolen(channels):
    key = bind(channels)
    assert channels.list_owned("bob") == channels.list_owned("admin") == []
    assert channels.active(key, "bob") is None
    assert "private-secret" not in channels.active(key)["credential"]
    with pytest.raises(ValueError):
        bind(channels, "bob")
    channels.unbind("admin", key)
    assert channels.active(key, "alice")
    assert channels.credentials(channels.active(key))["token"] == "private-secret"


def test_pairing_requires_website_confirmation_and_is_single_use(channels):
    pid, nonce = channels.new_pairing("alice", "telegram", {"bot_id": "42"})
    assert channels.pairing("bob", pid) is None
    with pytest.raises(ValueError):
        channels.confirm("alice", pid)
    channels.telegram_candidate(nonce, "WRONG", "10")
    assert channels.pairing("alice", pid)["status"] == "waiting"
    channels.telegram_candidate(nonce, "42", "10")
    channels.telegram_candidate(nonce, "42", "11")
    assert channels.pairing("alice", pid)["payload"]["peer_id"] == "10"
    assert channels.list_owned("alice") == []
    key = channels.confirm("alice", pid)
    assert channels.active(key)["peer_id"] == "10"
    with pytest.raises(ValueError):
        channels.confirm("alice", pid)


def test_pairing_expiry_and_disabled_account(channels):
    pid, _ = channels.new_pairing("alice", "weixin", {})
    channels.db.execute("UPDATE bot_pairings SET expires_at=0")
    assert channels.pairing("alice", pid) is None
    key = bind(channels)
    channels.db.execute("UPDATE account_memberships SET enabled=0 WHERE account='alice'")
    assert channels.active(key) is None
    channels.enqueue(key, "1", "ignored")
    assert not channels.db.query_all("SELECT * FROM bot_inbox")


def test_durable_dedup_and_queue_limit(channels):
    key = bind(channels)
    channels.enqueue(key, "1", "once", "context-secret")
    channels.enqueue(key, "1", "duplicate")
    reopened = bot_channels.ChannelStore(SQLiteStore(channels.db.db_path))
    rows = reopened.db.query_all("SELECT * FROM bot_inbox")
    assert len(rows) == 1 and rows[0]["text"] == "once"
    assert "context-secret" not in rows[0]["context"]
    for i in range(2, 21):
        channels.enqueue(key, str(i), "x")
    with pytest.raises(ValueError, match="队列已满"):
        channels.enqueue(key, "21", "x")
    channels.enqueue(key, "1", "duplicate when full")


def test_approval_is_conversation_bound_expiring_and_revoked(channels):
    a = bind(channels)
    b = bind(channels, "bob", bot_id="bot2", peer="peer2")
    code = channels.approval(a, "checkpoint")
    with pytest.raises(ValueError):
        channels.take_approval(b, code)
    assert channels.take_approval(a, code) == "checkpoint"
    with pytest.raises(ValueError):
        channels.take_approval(a, code)
    code = channels.approval(a, "expired")
    channels.db.execute("UPDATE bot_approvals SET expires_at=0")
    with pytest.raises(ValueError):
        channels.take_approval(a, code)
    code = channels.approval(a, "revoked")
    channels.unbind("alice", a)
    with pytest.raises(ValueError):
        channels.take_approval(a, code)


@pytest.mark.parametrize(
    "url",
    [
        "http://ilinkai.weixin.qq.com",
        "https://evil.example",
        "https://ilinkai.weixin.qq.com.evil.example",
        "https://ilinkai.weixin.qq.com@127.0.0.1",
        "https://ilinkai.weixin.qq.com:8080",
        "https://ilinkai.weixin.qq.com/other",
    ],
)
def test_weixin_rejects_untrusted_redirects(url):
    with pytest.raises(ValueError):
        bot_protocols.weixin_base(url)


def test_weixin_only_accepts_owner_private_final_text():
    message = {
        "message_type": 1,
        "message_state": 2,
        "from_user_id": "alice",
        "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
    }
    assert bot_protocols.weixin_text(message, "alice") == "hello"
    assert bot_protocols.weixin_text(message, "bob") is None
    assert bot_protocols.weixin_text({**message, "group_id": "group"}, "alice") is None
    assert bot_protocols.weixin_text({**message, "message_type": 2}, "alice") is None


def test_api_rejects_cross_account_pairing_and_unbind(channels, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="admin", name="admin", role="admin")
    pid, _ = channels.new_pairing("alice", "weixin", {})
    key = bind(channels)
    client = TestClient(app)
    assert client.post(f"/bot-channels/pairings/{pid}/poll", json={}).status_code == 404
    assert client.get("/bot-channels").json()["data"]["bindings"] == []
    assert client.delete(f"/bot-channels/bindings/{key}").status_code == 200
    assert channels.active(key)


def test_message_executes_as_bound_employee_and_confirmation_is_explicit(channels, monkeypatch):
    from app.api.routes import chat

    key = bind(channels)
    fake = AsyncMock(
        return_value={
            "data": {"content": "确认发消息到 A 群：hello", "metadata": {"approval_required": True, "resume_id": "cp"}}
        }
    )
    monkeypatch.setattr(chat, "chat", fake)
    reply = asyncio.run(bot_runner.process_message(channels.active(key), "发消息"))
    req, account = fake.call_args.args
    assert account.account == "alice" and account.role == "employee"
    assert req.session_id == channels.active(key)["session_id"] and not req.confirm_write
    code = reply.split("/confirm ")[1].splitlines()[0]
    fake.return_value = {"data": {"content": "sent", "metadata": {}}}
    assert asyncio.run(bot_runner.process_message(channels.active(key), f"/confirm {code}")) == "sent"
    assert fake.call_args.args[0].resume_id == "cp"
    assert fake.call_args.args[0].confirm_write
    with pytest.raises(ValueError):
        asyncio.run(bot_runner.process_message(channels.active(key), f"/confirm {code}"))


def test_long_approval_does_not_send_code_beside_truncated_details(channels, monkeypatch):
    from app.api.routes import chat

    key = bind(channels)
    monkeypatch.setattr(
        chat,
        "chat",
        AsyncMock(
            return_value={"data": {"content": "x" * 1500, "metadata": {"approval_required": True, "resume_id": "cp"}}}
        ),
    )
    reply = asyncio.run(bot_runner.process_message(channels.active(key), "write"))
    assert "/confirm" not in reply and "网站" in reply
    assert not channels.db.query_all("SELECT * FROM bot_approvals")


def test_reply_failure_never_reexecutes_task(channels, monkeypatch):
    key = bind(channels)
    channels.enqueue(key, "one", "read", "context")
    row = dict(channels.db.query_one("SELECT * FROM bot_inbox"))
    execute = AsyncMock(return_value="result")
    send = AsyncMock(side_effect=httpx.ReadTimeout("uncertain"))
    monkeypatch.setattr(bot_runner, "process_message", execute)
    monkeypatch.setattr(bot_runner, "send_reply", send)
    asyncio.run(bot_runner.BotRunner().handle(channels.active(key), row))
    assert execute.await_count == 1 and send.await_count == 1
    assert channels.db.query_one("SELECT status FROM bot_inbox")["status"] == "delivery_unknown"


def test_guard_rechecks_binding_before_next_feishu_operation(channels, monkeypatch):
    from app.core import account_access

    monkeypatch.setattr(account_access, "store", channels.db)
    key = bind(channels)
    marker = bot_channels.channel_execution.set(key)
    try:
        assert account_access.account_execution_error("alice") == ""
        assert account_access.account_execution_error("bob")
        channels.unbind("alice", key)
        assert account_access.account_execution_error("alice")
    finally:
        bot_channels.channel_execution.reset(marker)


def test_disabling_member_atomically_revokes_bindings_and_pending_work(channels, monkeypatch):
    from app.api.routes import auth

    monkeypatch.setattr(auth, "store", channels.db)
    key = bind(channels)
    channels.enqueue(key, "1", "queued")
    channels.approval(key, "checkpoint")
    asyncio.run(
        auth.update_member(
            "alice", auth.MemberUpdate(enabled=False), AccountInfo(account="admin", name="admin", role="admin")
        )
    )
    assert channels.list_owned("alice") == []
    assert channels.db.query_all("SELECT * FROM bot_inbox") == []
    assert channels.db.query_all("SELECT * FROM bot_approvals") == []
    asyncio.run(
        auth.update_member(
            "alice", auth.MemberUpdate(enabled=True), AccountInfo(account="admin", name="admin", role="admin")
        )
    )
    assert channels.active(key) is None


def test_telegram_polling_ignores_group_and_other_user_and_deduplicates(channels, monkeypatch):
    key = bind(channels, channel="telegram", bot_id="42", peer="10")
    message = {"date": channels.db.now(), "chat": {"type": "private", "id": 10}, "from": {"id": 10}, "text": "query"}
    updates = [
        {"update_id": 1, "message": message},
        {"update_id": 1, "message": message},
        {"update_id": 2, "message": {**message, "chat": {"type": "group", "id": -1}}},
        {"update_id": 3, "message": {**message, "from": {"id": 11}}},
    ]
    calls = 0

    async def remote(token, method, payload=None):
        nonlocal calls
        if method == "getMe":
            return {"id": 42}
        calls += 1
        if calls == 1:
            return updates
        raise asyncio.CancelledError()

    monkeypatch.setattr(bot_runner, "telegram", remote)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(bot_runner.BotRunner().poll_telegram())
    rows = channels.db.query_all("SELECT * FROM bot_inbox")
    assert len(rows) == 1 and rows[0]["binding_id"] == key and rows[0]["text"] == "query"


def test_runner_serializes_employee_but_allows_another_employee(channels, monkeypatch):
    a = bind(channels)
    b = bind(channels, "bob", bot_id="bot2", peer="peer2")
    channels.enqueue(a, "1", "a-first")
    channels.enqueue(a, "2", "a-second")
    channels.enqueue(b, "1", "b-first")

    async def scenario():
        first_started, release, bob_done, second_done = (asyncio.Event() for _ in range(4))
        order = []

        async def execute(binding, text):
            order.append(text)
            if text == "a-first":
                first_started.set()
                await release.wait()
            elif text == "a-second":
                assert release.is_set()
                second_done.set()
            else:
                bob_done.set()
            return "ok"

        async def idle(_key):
            await asyncio.Event().wait()

        monkeypatch.setattr(bot_runner, "process_message", execute)
        monkeypatch.setattr(bot_runner, "send_reply", AsyncMock())
        runner = bot_runner.BotRunner()
        monkeypatch.setattr(runner, "poll_weixin", idle)
        runner.start()
        try:
            await asyncio.wait_for(first_started.wait(), 3)
            await asyncio.wait_for(bob_done.wait(), 3)
            assert not second_done.is_set()
            release.set()
            await asyncio.wait_for(second_done.wait(), 3)
            assert order.count("a-first") == order.count("a-second") == order.count("b-first") == 1
        finally:
            await runner.stop()

    asyncio.run(scenario())


def test_weixin_transport_headers_payload_and_api_error(monkeypatch):
    import json

    actual_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"ret": 0, "msgs": [], "get_updates_buf": "next"})

    monkeypatch.setattr(
        bot_protocols.httpx, "AsyncClient", lambda **kw: actual_client(transport=httpx.MockTransport(handle), **kw)
    )
    result = asyncio.run(
        bot_protocols.weixin("POST", "ilink/bot/getupdates", token="secret", payload={"get_updates_buf": "old"})
    )
    assert result["get_updates_buf"] == "next"
    assert requests[0].headers["Authorization"] == "Bearer secret"
    assert requests[0].headers["iLink-App-Id"] == "bot"
    assert json.loads(requests[0].content)["get_updates_buf"] == "old"
    assert json.loads(requests[0].content)["base_info"]["bot_agent"] == "FeishuCLIWeb/0.1.0"


def test_qr_api_scanning_does_not_expose_token_or_bind_before_confirmation(channels, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_account] = lambda: AccountInfo(account="alice", name="alice")
    remote = AsyncMock(
        side_effect=[
            {"qrcode": "challenge", "qrcode_img_content": "https://weixin.qq.com/test"},
            {
                "status": "confirmed",
                "bot_token": "secret",
                "ilink_bot_id": "mybot",
                "ilink_user_id": "myself",
                "baseurl": bot_protocols.WEIXIN_BASE,
            },
        ]
    )
    monkeypatch.setattr(routes, "weixin", remote)
    client = TestClient(app)
    response = client.post("/bot-channels/weixin/pair")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    pid = response.json()["data"]["id"]
    assert response.json()["data"]["image"].startswith("data:image/svg+xml;base64,")
    result = client.post(f"/bot-channels/pairings/{pid}/poll", json={})
    assert result.json()["data"] == {"status": "scanned", "peer_id": "myself"}
    assert "secret" not in result.text
    assert channels.list_owned("alice") == []
    assert client.post(f"/bot-channels/pairings/{pid}/confirm").status_code == 200
    assert len(channels.list_owned("alice")) == 1
