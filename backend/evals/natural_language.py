"""Opt-in real-model evaluation with synthetic Feishu observations, never live writes.

Run from backend: .venv/bin/python -m evals.natural_language --live-model
Requires the configured model key; model API usage may incur cost. CLI subprocesses
are limited to tool help/schema inspection. All business execution uses FakeFeishu.
This evaluates language/planning, NOT real Feishu permissions or API behavior.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import tempfile
import time
from types import SimpleNamespace
from typing import Any


class FakeFeishu:
    def __init__(self, ambiguous: bool = False) -> None:
        self.calls: list[str] = []
        self.sent: list[dict[str, str]] = []
        self.receipts: dict[str, dict[str, str]] = {}
        self.ambiguous = ambiguous

    async def execute(self, command: str, *args: Any, **kwargs: Any) -> tuple[bool, str, str]:
        self.calls.append(command)
        parts = shlex.split(command)
        flags: dict[str, str] = {}
        for index, value in enumerate(parts[:-1]):
            if value.startswith("--"):
                flags[value[2:]] = parts[index + 1]
        operation = " ".join(parts[1:3])
        chats = [{"chat_id": "oc_evalproject", "name": "项目组", "user_count": 12, "bot_count": 1}]
        if self.ambiguous:
            chats.append({"chat_id": "oc_evalother", "name": "项目组", "user_count": 9, "bot_count": 0})
        if operation in {"im +chat-search", "im +chat-list"}:
            # Live search returns chats, not member counts; getting a count must
            # require a separate detail read rather than an unrealistically rich fixture.
            data: Any = {"chats": [{"chat_id": chat["chat_id"], "name": chat["name"]} for chat in chats], "has_more": False}
        elif parts[1:4] == ["im", "chats", "get"]:
            params = json.loads(flags.get("params", "{}"))
            matched = next((chat for chat in chats if chat["chat_id"] == params.get("chat_id")), None)
            if matched is None:
                return False, "", "synthetic resource not found"
            data = matched
        elif operation == "im +chat-members-list":
            if flags.get("chat-id") != "oc_evalproject":
                return False, "", "synthetic resource not found"
            data = {"items": [{"member_id": "ou_evallin", "member_id_type": "open_id", "name": "小林"}],
                    "has_more": False}
        elif operation == "im +messages-send":
            if flags.get("chat-id") != "oc_evalproject" or not kwargs.get("approved_write"):
                return False, "", "wrong target or missing approval"
            key = flags.get("idempotency-key", "")
            if not key.startswith("web-"):
                return False, "", "missing server idempotency key"
            try:
                payload = json.loads(flags["content"]) if "content" in flags else {"text": flags.get("text")}
                if flags.get("msg-type", "text") == "text":
                    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
                        raise ValueError("text payload must contain a string")
                    mentions = re.findall(r'<at user_id="([^"]+)"', payload["text"])
                elif flags.get("msg-type") == "post":
                    if not isinstance(payload, dict) or not payload or "post" in payload:
                        raise ValueError("post payload must be a direct language object")
                    mentions = []
                    for language, body in payload.items():
                        if not re.fullmatch(r"[a-z]{2}_[a-z]{2}", language) or not isinstance(body, dict):
                            raise ValueError("invalid language object")
                        rows = body.get("content")
                        if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
                            raise ValueError("post content must contain rows")
                        for row in rows:
                            for node in row:
                                if not isinstance(node, dict) or not isinstance(node.get("tag"), str):
                                    raise ValueError("invalid post node")
                                if node["tag"] == "at":
                                    mentions.append(node.get("user_id"))
                else:
                    raise ValueError("unsupported synthetic message type")
                if any(mention != "ou_evallin" for mention in mentions):
                    raise ValueError("mention not in synthetic group")
            except (ValueError, TypeError) as error:
                return False, "", str(error)
            if key in self.receipts:
                if flags != self.receipts[key]:
                    return False, "", "idempotency key reused with different content"
                return True, json.dumps({"ok": True, "data": {"message_id": "om_evalsent", "chat_id": "oc_evalproject"}}), ""
            self.receipts[key] = flags
            self.sent.append(flags)
            data = {"message_id": "om_evalsent", "chat_id": "oc_evalproject"}
        else:
            return False, "", "Synthetic fixture does not support this operation; do not assume success."
        return True, json.dumps({"ok": True, "data": data}, ensure_ascii=False), ""


async def evaluate(case_ids: list[str]) -> list[dict[str, Any]]:
    # Set the temporary store BEFORE importing application modules. No production
    # accounts, history, grants, schedules or checkpoints can enter this evaluation.
    from app.config import get_settings
    from app.core import feishu_tokens
    from app.core.storage import store
    from app.skills.base import SkillContext
    from app.skills.lark_cli import agent_graph as graph

    real_call_model = graph.call_model
    last_messages: list[dict[str, Any]] = []

    async def observed_model(model_settings: Any, messages: list[dict[str, Any]]) -> dict[str, Any]:
        nonlocal last_messages
        last_messages = messages
        return await real_call_model(model_settings, messages)

    graph.call_model = observed_model

    settings = get_settings()
    if not (settings.ANTHROPIC_API_KEY if settings.LLM_PROVIDER == "anthropic" else settings.OPENAI_API_KEY):
        raise SystemExit("No configured model key. No evaluation was run.")
    store.execute("INSERT INTO accounts VALUES ('eval-user', 'Synthetic', '', 1, 1)")
    store.execute("INSERT INTO account_memberships(account,role,updated_at) VALUES ('eval-user','employee',1)")
    from app.core.feishu_permissions import COMMANDS, required_command_scopes
    scopes = sorted(set().union(*(required_command_scopes(["lark-cli", *path], caps) for path, caps in COMMANDS.items())))
    store.execute("""INSERT INTO feishu_grants(account,encrypted_token,scopes,expires_at,refresh_expires_at,updated_at)
        VALUES ('eval-user', '', ?, 9999999999, 9999999999, 1)""", (" ".join(scopes),))
    feishu_tokens.enterprise_configured = lambda: False
    cases = [
        {"id": "typo-count", "query": "帮我瞅瞅项目组这个群有机个人啊", "expected": "completed"},
        {"id": "referent-mention", "query": "在刚才那个群艾特小林说：明天下午三点开会。现在就发，不是定时发送。",
         "history": [{"role": "user", "content": "找一下项目组"},
                     {"role": "assistant", "content": "找到项目组，群 ID 是 oc_evalproject。"}],
         "expected": "completed", "write": True},
        {"id": "ambiguous-group", "query": "给项目组群发个通知：下午开会。", "expected": "clarification", "ambiguous": True},
    ]
    report = []
    for case in cases:
        if case_ids and case["id"] not in case_ids:
            continue
        last_messages = []
        fixture = FakeFeishu(bool(case.get("ambiguous")))
        skill = SimpleNamespace(settings=settings, execute_command=fixture.execute,
                                _make_progress_update=lambda text: {"type": "progress", "content": text},
                                _build_scope_setup_metadata=lambda state, scopes: {"setup_required": True})
        context = SkillContext(user_id="eval-user", session_id=case["id"], message=case["query"],
                               history=case.get("history", []))
        start = time.monotonic()
        approvals = 0
        for _ in range(5):
            events = [event async for event in graph.execute_agent(skill, context, context.message, None)]
            result = events[-1]["result"]
            if not result.data.get("approval_required"):
                break
            if not case.get("write"):
                break  # Never approve an unexpected action, even in the fixture.
            approvals += 1
            context.metadata = {"resume_checkpoint": {"agent_run_id": result.data["agent_run_id"]}, "approve_tool": True}
        passed = result.data.get("run_state") == case["expected"]
        if case.get("write"):
            passed = passed and len(fixture.sent) == 1 and approvals == 1
            if fixture.sent:
                content = fixture.sent[0].get("content", fixture.sent[0].get("text", ""))
                passed = passed and "ou_evallin" in content and "明天下午三点开会" in content
        else:
            passed = passed and not fixture.sent and not result.data.get("approval_required")
        if case["id"] == "typo-count":
            passed = passed and "12" in result.message and not re.search(r"(?:共|有|总共|共有)\s*13\s*人", result.message)
        errors = []
        for message in last_messages:
            if message.get("role") == "tool":
                observation = json.loads(message["content"])
                if isinstance(observation, dict) and observation.get("error"):
                    errors.append(observation)
        row = {"case": case["id"], "passed": bool(passed), "outcome": result.data.get("run_state"),
               "seconds": round(time.monotonic() - start, 2), "tool_calls": len(fixture.calls),
               "approvals": approvals, "answer": result.message, "commands": fixture.calls,
               "timings": result.data.get("plan", {}).get("timings", []),
               "tool_errors": errors}
        report.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    print(json.dumps({"model": settings.LLM_MODEL, "passed": sum(row["passed"] for row in report),
                      "total": len(report), "real_feishu_calls": 0}, ensure_ascii=False), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true", help="Explicitly allow configured model API calls")
    parser.add_argument("--case", action="append", choices=["typo-count", "referent-mention", "ambiguous-group"], default=[])
    args = parser.parse_args()
    if not args.live_model:
        parser.error("--live-model is required; this evaluation calls the configured model API")
    with tempfile.TemporaryDirectory(prefix="feishu-model-eval-") as directory:
        os.environ["FEISHU_CLI_DATA_DIR"] = directory
        results = asyncio.run(evaluate(args.case))
    raise SystemExit(0 if all(row["passed"] for row in results) else 1)


if __name__ == "__main__":
    main()
