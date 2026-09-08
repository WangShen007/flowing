import asyncio
import json
import shlex

from evals.natural_language import FakeFeishu


def command(content, key="web-eval", kind="post"):
    return shlex.join(["lark-cli", "im", "+messages-send", "--chat-id", "oc_evalproject", "--msg-type", kind,
                       "--content", json.dumps(content), "--idempotency-key", key])


def test_synthetic_sender_rejects_non_api_post_wrapper():
    fixture = FakeFeishu()
    payload = {"post": {"zh_cn": {"content": [[{"tag": "text", "text": "hello"}]]}}}
    success, _, _ = asyncio.run(fixture.execute(command(payload), approved_write=True))
    assert not success
    assert not fixture.sent


def test_synthetic_sender_deduplicates_only_approved_identical_payload():
    fixture = FakeFeishu()
    payload = {"zh_cn": {"content": [[{"tag": "at", "user_id": "ou_evallin"}]]}}
    assert not asyncio.run(fixture.execute(command(payload)))[0]
    assert not asyncio.run(fixture.execute(command(payload, key=""), approved_write=True))[0]
    assert asyncio.run(fixture.execute(command(payload), approved_write=True))[0]
    assert asyncio.run(fixture.execute(command(payload), approved_write=True))[0]
    assert len(fixture.sent) == 1
    assert not asyncio.run(fixture.execute(command({"text": "changed"}, kind="text"), approved_write=True))[0]
