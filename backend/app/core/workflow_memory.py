from __future__ import annotations

import hashlib
import re
import shlex
from copy import deepcopy
from typing import Any

from app.core.intent_resolution import normalize_action_typos
from app.core.local_sessions import LocalSessionStore
from app.core.storage import store

MEMORY_SCHEMA_VERSION = 1
AUTO_ACTIVATE_SUCCESSES = 2
AUTO_DISABLE_FAILURES = 2
_FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "approval": ("审批", "批准"),
    "calendar": ("日历", "日程", "开会", "会议"),
    "conditional": ("如果", "有的话", "一旦", "没有的话"),
    "contact": ("联系人", "同事", "成员", "参与人", "参会人"),
    "create": ("创建", "新建", "建立"),
    "document": ("文档", "知识库", "wiki"),
    "group": ("群", "群聊", "群组"),
    "message": ("消息", "通知", "知会", "告诉", "提醒"),
    "read": ("读取", "查看", "查询", "看看", "搜索", "找"),
    "recurring": ("每天", "每日", "每逢", "工作日", "定期", "以后"),
    "sheet": ("表格", "多维表格", "电子表格", "sheet", "base"),
    "summary": ("汇总", "总结", "整理", "归纳"),
    "task": ("任务", "待办", "阻塞", "事项"),
    "unanswered": ("未回复", "没回复", "没人回应", "未回应"),
    "write": ("发送", "发消息", "发通知", "更新", "修改", "删除", "上传", "导入"),
}
_INTENT_LABELS = {
    "group_message": "群消息通知",
    "direct_message": "联系人消息",
    "scheduled_task": "定时工作流",
    "calendar_create": "日程创建",
    "group_create": "群聊创建",
    "document_create": "文档创建",
}
_DYNAMIC_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:o(?:c|u|m|mt)_[A-Za-z0-9_-]+|"
    r"(?:doxcn|doccn|shtcn|bascn|fldcn|wikcn|tbl|rec)[A-Za-z0-9_-]{6,})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_DYNAMIC_ID_FLAG_RE = re.compile(
    r"--(?:chat-id|user-id|open-id|document-id|folder-token|wiki-token|"
    r"app-token|table-id|record-id|message-id|event-id|thread)(?:\s|=)",
    re.IGNORECASE,
)
_WRITE_COMMAND_RE = re.compile(
    r"(?:\+[a-z0-9-]*(?:send|reply|create|update|delete|remove|upload|download|"
    r"import|export|move|invite|add|edit|rename)\b|\bapi\s+(?:POST|PUT|PATCH|DELETE)\b)",
    re.IGNORECASE,
)


def _normalized_request(query: str) -> str:
    normalized = normalize_action_typos(query).normalized_query.strip().lower()
    return re.sub(r"\s+", " ", normalized)


def _request_hash(query: str) -> str:
    return hashlib.sha256(_normalized_request(query).encode("utf-8")).hexdigest()


def _request_features(query: str) -> set[str]:
    text = _normalized_request(query)
    features = {
        feature
        for feature, aliases in _FEATURE_ALIASES.items()
        if any(alias.lower() in text for alias in aliases)
    }
    if re.search(r"(?:今天|明天|后天|\d{1,2}\s*[:：点时]|[一二两三四五六七八九十]+点)", text):
        features.add("time")
    if re.search(r"(?:然后|接着|再|并且|并|同时)", text):
        features.add("multi_step")
    return features


def _request_pattern(query: str) -> str:
    features = sorted(_request_features(query))
    return "|".join(features) if features else "unclassified"


def _command_shape(command: str) -> str:
    try:
        args = shlex.split(command)
    except ValueError:
        return ""
    if not args or args[0] != "lark-cli":
        return ""
    if len(args) >= 3:
        return " ".join(args[:3])
    return " ".join(args)


def _plan_blueprint(plan: dict[str, Any], executed_commands: list[dict[str, Any]]) -> dict[str, Any]:
    commands = list(plan.get("commands") or []) + executed_commands
    shapes: list[str] = []
    for item in commands:
        if not isinstance(item, dict):
            continue
        shape = _command_shape(str(item.get("command") or ""))
        if shape and shape not in shapes:
            shapes.append(shape)
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "intent_type": str(plan.get("intent_type") or plan.get("type") or ""),
        "relevant_skills": [str(item) for item in (plan.get("relevant_skills") or []) if item],
        "command_shapes": shapes,
        "procedure": str(plan.get("procedure") or "")[:1500],
        "requires_confirmation": bool(plan.get("need_confirmation"))
        or any(bool(item.get("write")) for item in commands if isinstance(item, dict)),
    }


def _intent_key(plan: dict[str, Any], blueprint: dict[str, Any]) -> str:
    intent_type = str(plan.get("intent_type") or plan.get("type") or "").strip()
    if intent_type and intent_type != "agent_workflow":
        return intent_type[:80]
    shapes = blueprint.get("command_shapes") or []
    if shapes:
        digest = hashlib.sha256(">".join(shapes).encode("utf-8")).hexdigest()[:16]
        return f"commands:{digest}"
    return ""


def _memory_label(intent_key: str, blueprint: dict[str, Any]) -> str:
    if intent_key in _INTENT_LABELS:
        return _INTENT_LABELS[intent_key]
    skills = [str(item).removeprefix("lark-") for item in blueprint.get("relevant_skills") or []]
    visible = " → ".join(skills[:3])
    return f"{visible} 工作流" if visible else "自定义飞书工作流"


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _safe_reusable_commands(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only side-effect-free commands whose targets do not need refreshing."""

    commands: list[dict[str, Any]] = []
    for item in plan.get("commands") or []:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        expected = str(item.get("expected") or "read").lower()
        if expected not in {"read", "search", "schema"}:
            continue
        if not _command_shape(command):
            continue
        if _WRITE_COMMAND_RE.search(command):
            continue
        if _DYNAMIC_ID_FLAG_RE.search(command) or _DYNAMIC_ID_RE.search(command):
            continue
        reusable_item = {"command": command, "expected": expected}
        reason = str(item.get("reason") or "").strip()
        if reason:
            reusable_item["reason"] = reason
        commands.append(reusable_item)
    return commands


class WorkflowMemoryStore:
    def record_success(
        self,
        *,
        user_id: str,
        execution_record_id: int,
        request: str,
        plan: dict[str, Any],
        executed_commands: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if plan.get("requires_input") or plan.get("setup_required"):
            return None
        if plan.get("planning_source") == "langgraph" and plan.get("run_state") != "completed":
            return None
        if plan.get("planning_source") == "langgraph":
            # The server marks read/write kind. A recovered read failure is not
            # a failed overall goal, but must not enter the reusable procedure.
            if any(not item.get("success") and item.get("expected") != "read"
                   for item in executed_commands):
                return None
            executed_commands = [item for item in executed_commands if item.get("success")]
            if not executed_commands:
                return None
            plan = {**plan, "commands": executed_commands}
        elif executed_commands and not all(
            item.get("success") or item.get("superseded_by_repair") for item in executed_commands
        ):
            return None

        blueprint = _plan_blueprint(plan, executed_commands)
        intent_key = _intent_key(plan, blueprint)
        if not intent_key or (not blueprint["command_shapes"] and intent_key != "scheduled_task"):
            return None

        safe_user = LocalSessionStore._safe_user_id(user_id)
        pattern = _request_pattern(request)
        request_hash = _request_hash(request)
        now = store.now()
        with store.connect() as conn:
            # Multiple conversations for one account can finish concurrently.
            # Serialize the read/upgrade/insert decision so success counts are
            # never lost and the unique workflow key cannot race.
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM workflow_memories WHERE user_id = ? AND intent_key = ? AND request_pattern = ?",
                (safe_user, intent_key, pattern),
            ).fetchone()
            if existing and existing["last_execution_record_id"] == execution_record_id:
                return self._row_to_dict(existing)

            if existing:
                if existing["status"] == "disabled":
                    # A later independently successful run is fresh evidence,
                    # but must start as a candidate rather than silently
                    # restoring an automatically or manually disabled memory.
                    success_count = 1
                    status = "candidate"
                else:
                    success_count = int(existing["success_count"]) + 1
                    status = (
                        "active"
                        if existing["status"] == "active" or success_count >= AUTO_ACTIVATE_SUCCESSES
                        else "candidate"
                    )
                conn.execute(
                    """
                    UPDATE workflow_memories
                    SET last_request_hash = ?, label = ?, blueprint_json = ?, status = ?,
                        success_count = ?, failure_count = 0, last_execution_record_id = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        request_hash,
                        _memory_label(intent_key, blueprint),
                        store.dumps(blueprint),
                        status,
                        success_count,
                        execution_record_id,
                        now,
                        existing["id"],
                        safe_user,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM workflow_memories WHERE id = ? AND user_id = ?",
                    (existing["id"], safe_user),
                ).fetchone()
                return self._row_to_dict(row)

            cursor = conn.execute(
                """
                INSERT INTO workflow_memories(
                    user_id, intent_key, request_pattern, last_request_hash, label,
                    blueprint_json, status, success_count, failure_count,
                    last_execution_record_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'candidate', 1, 0, ?, ?, ?)
                """,
                (
                    safe_user,
                    intent_key,
                    pattern,
                    request_hash,
                    _memory_label(intent_key, blueprint),
                    store.dumps(blueprint),
                    execution_record_id,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workflow_memories WHERE id = ? AND user_id = ?",
                (int(cursor.lastrowid), safe_user),
            ).fetchone()
            return self._row_to_dict(row)

    def record_failure(self, user_id: str, memory_id: int) -> dict[str, Any] | None:
        safe_user = LocalSessionStore._safe_user_id(user_id)
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            memory = conn.execute(
                "SELECT * FROM workflow_memories WHERE id = ? AND user_id = ?",
                (memory_id, safe_user),
            ).fetchone()
            if not memory:
                return None
            failures = int(memory["failure_count"]) + 1
            status = "disabled" if failures >= AUTO_DISABLE_FAILURES else memory["status"]
            conn.execute(
                "UPDATE workflow_memories SET failure_count = ?, status = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (failures, status, store.now(), memory_id, safe_user),
            )
            row = conn.execute(
                "SELECT * FROM workflow_memories WHERE id = ? AND user_id = ?",
                (memory_id, safe_user),
            ).fetchone()
            return self._row_to_dict(row)

    def activate(self, user_id: str, memory_id: int) -> dict[str, Any] | None:
        safe_user = LocalSessionStore._safe_user_id(user_id)
        store.execute(
            """
            UPDATE workflow_memories SET status = 'active', failure_count = 0, updated_at = ?
            WHERE id = ? AND user_id = ? AND status != 'disabled'
            """,
            (store.now(), memory_id, safe_user),
        )
        return self.get_for_user(safe_user, memory_id)

    def disable(self, user_id: str, memory_id: int) -> bool:
        safe_user = LocalSessionStore._safe_user_id(user_id)
        memory = self.get_for_user(safe_user, memory_id)
        if not memory:
            return False
        store.execute(
            "UPDATE workflow_memories SET status = 'disabled', updated_at = ? WHERE id = ? AND user_id = ?",
            (store.now(), memory_id, safe_user),
        )
        return True

    def get_for_user(
        self, user_id: str, memory_id: int, *, include_disabled: bool = False
    ) -> dict[str, Any] | None:
        safe_user = LocalSessionStore._safe_user_id(user_id)
        status_clause = "" if include_disabled else " AND status != 'disabled'"
        row = store.query_one(
            f"SELECT * FROM workflow_memories WHERE id = ? AND user_id = ?{status_clause}",
            (memory_id, safe_user),
        )
        return self._row_to_dict(row) if row else None

    def list_for_user(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_user = LocalSessionStore._safe_user_id(user_id)
        rows = store.query_all(
            """
            SELECT * FROM workflow_memories
            WHERE user_id = ? AND status != 'disabled'
            ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, updated_at DESC
            LIMIT ?
            """,
            (safe_user, max(1, min(limit, 200))),
        )
        return [self._row_to_dict(row) for row in rows]

    def list_public_for_user(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return [self.public_view(memory) for memory in self.list_for_user(user_id, limit)]

    def find_matches(self, user_id: str, query: str, limit: int = 3) -> list[dict[str, Any]]:
        safe_user = LocalSessionStore._safe_user_id(user_id)
        query_hash = _request_hash(query)
        query_features = _request_features(query)
        rows = store.query_all(
            """
            SELECT * FROM workflow_memories
            WHERE user_id = ? AND status = 'active'
            ORDER BY updated_at DESC
            LIMIT 100
            """,
            (safe_user,),
        )
        matches: list[dict[str, Any]] = []
        for row in rows:
            exact = row["last_request_hash"] == query_hash
            stored_features = {item for item in str(row["request_pattern"]).split("|") if item}
            score = 1.0 if exact else _similarity(query_features, stored_features)
            if not exact and score < 0.6:
                continue
            memory = self._row_to_dict(row)
            memory.update({"exact_match": exact, "match_score": round(score, 3)})
            matches.append(memory)
        matches.sort(key=lambda item: (-float(item["match_score"]), -int(item["updated_at"])))
        return matches[: max(1, min(limit, 10))]

    def reusable_plan(self, user_id: str, memory: dict[str, Any], query: str) -> dict[str, Any] | None:
        if not memory.get("exact_match") or memory.get("status") != "active":
            return None
        if memory.get("last_request_hash") != _request_hash(query):
            return None
        record_id = memory.get("last_execution_record_id")
        row = store.query_one(
            "SELECT plan_json FROM execution_records WHERE id = ? AND user_id = ? AND success = 1",
            (record_id, LocalSessionStore._safe_user_id(user_id)),
        )
        plan = store.loads(row["plan_json"], {}) if row else {}
        if not isinstance(plan, dict) or not plan:
            return None
        reusable = deepcopy(plan)
        reusable["commands"] = _safe_reusable_commands(reusable)
        reusable.pop("workflow_memory", None)
        return reusable

    def mark_used(self, user_id: str, memory_id: int) -> None:
        safe_user = LocalSessionStore._safe_user_id(user_id)
        now = store.now()
        store.execute(
            "UPDATE workflow_memories SET last_used_at = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (now, now, memory_id, safe_user),
        )

    @staticmethod
    def public_view(memory: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": memory["id"],
            "intent_key": memory["intent_key"],
            "label": memory["label"],
            "status": memory["status"],
            "success_count": memory["success_count"],
            "failure_count": memory["failure_count"],
            "last_used_at": memory["last_used_at"],
            "created_at": memory["created_at"],
            "updated_at": memory["updated_at"],
        }

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "intent_key": row["intent_key"],
            "request_pattern": row["request_pattern"],
            "last_request_hash": row["last_request_hash"],
            "label": row["label"],
            "blueprint": store.loads(row["blueprint_json"], {}),
            "status": row["status"],
            "success_count": int(row["success_count"]),
            "failure_count": int(row["failure_count"]),
            "last_execution_record_id": row["last_execution_record_id"],
            "last_used_at": row["last_used_at"],
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }


workflow_memory_store = WorkflowMemoryStore()
