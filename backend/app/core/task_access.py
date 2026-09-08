"""Task ownership is checked against fresh Feishu data, not model assertions."""
from __future__ import annotations

import json
import re
import shlex
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from app.config import get_settings
from app.core.feishu_permissions import member_role
from app.core.storage import store

TASK_MUTATIONS = {("task", "+update"), ("task", "+complete"), ("task", "+assign"),
                  ("task", "tasks", "delete")}
GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def mutation_path(args: list[str]) -> tuple[str, ...] | None:
    return next((path for path in TASK_MUTATIONS if tuple(args[1:1 + len(path)]) == path), None)


def flag_value(args: list[str], name: str) -> str:
    values = []
    for i, arg in enumerate(args):
        if arg == name:
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                raise ValueError(f"缺少参数 {name}")
            values.append(args[i + 1])
        elif arg.startswith(name + "="):
            values.append(arg.split("=", 1)[1])
    if len(values) != 1:
        raise ValueError(f"参数 {name} 必须明确指定一次")
    return values[0]


def target_ids(args: list[str], path: tuple[str, ...]) -> list[str]:
    if path[-1] == "delete":
        params = json.loads(flag_value(args, "--params"))
        if not isinstance(params, dict) or set(params) != {"task_guid"}:
            raise ValueError("删除任务仅接受明确的 task_guid")
        raw = params["task_guid"]
    else:
        raw = flag_value(args, "--task-id")
    if not isinstance(raw, str):
        raise ValueError("任务标识必须是字符串")
    ids = []
    for value in raw.split(","):
        value = value.strip()
        if value.startswith("https://"):
            url = urlparse(value)
            if url.hostname != "applink.feishu.cn" or url.path != "/client/todo/detail":
                raise ValueError("请使用任务 GUID 或飞书任务链接")
            guids = parse_qs(url.query).get("guid", [])
            value = guids[0] if len(guids) == 1 else ""
        if not GUID.fullmatch(value):
            raise ValueError("任务 GUID 无效，不能猜测任务归属")
        ids.append(value)
    if len(ids) != len(set(ids)) or len(ids) > 50 or (path[-1] != "+update" and len(ids) != 1):
        raise ValueError("任务目标重复、过多或该操作不支持批量执行")
    return ids


def task_decision(task: dict, open_id: str, role: str, action: str, managed_lists: set[str]) -> str:
    creator = task.get("creator") or {}
    if creator.get("type") == "user" and creator.get("id") == open_id:
        return "creator"
    if role == "admin" and any(isinstance(item, dict) and item.get("tasklist_guid") in managed_lists
                               for item in task.get("tasklists", [])):
        return "managed_tasklist_admin"
    if action == "+complete" and any(isinstance(item, dict) and item.get("type") == "user"
                                      and item.get("role") == "assignee" and item.get("id") == open_id
                                      for item in task.get("members", [])):
        return "assignee_complete"
    raise ValueError("任务权限不足：仅创建者可改删，执行人仅可标记完成；管理员仅可管理已配置清单内的任务")


async def enforce_task_access(account: str, args: list[str],
                              read: Callable[[str], Awaitable[tuple[bool, str, str]]]) -> None:
    path = mutation_path(args)
    if path is None:
        return
    ids = target_ids(args, path)
    settings = get_settings()
    identity = store.query_one("SELECT open_id,app_id,tenant_key FROM feishu_identities WHERE account=?", (account,))
    if not identity or identity["app_id"] != settings.FEISHU_APP_ID or identity["tenant_key"] != settings.FEISHU_TENANT_KEY:
        raise ValueError("无法验证当前飞书身份，禁止修改任务")
    managed = {value.strip() for value in settings.FEISHU_MANAGED_TASKLIST_IDS.split(",") if value.strip()}
    # All targets must pass before the CLI can write any one of them.
    store.execute("""CREATE TABLE IF NOT EXISTS task_permission_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, task_guid TEXT NOT NULL,
        action TEXT NOT NULL, decision TEXT NOT NULL, created_at INTEGER NOT NULL)""")
    for guid in ids:
        decision = "denied"
        try:
            command = shlex.join(["lark-cli", "task", "tasks", "get", "--params",
                                  json.dumps({"task_guid": guid, "user_id_type": "open_id"}),
                                  "--as", "user", "--format", "json"])
            ok, stdout, _ = await read(command)
            if not ok:
                raise ValueError("无法读取任务详情验证归属，未执行任务修改")
            envelope = json.loads(stdout)
            if envelope.get("ok") is not True:
                raise ValueError("任务详情未返回成功结果，禁止修改")
            task = envelope.get("data", {}).get("task")
            if not isinstance(task, dict) or task.get("guid") != guid:
                raise ValueError("任务详情与目标不匹配，禁止修改")
            # Fetch role after the network read, so a demotion during the read
            # cannot use an older manager decision.
            decision = task_decision(task, identity["open_id"], member_role(account), path[-1], managed)
        finally:
            store.execute("INSERT INTO task_permission_audit(account,task_guid,action,decision,created_at) VALUES (?,?,?,?,?)",
                          (account, guid, path[-1], decision, store.now()))
