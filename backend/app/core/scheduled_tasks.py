from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.core.execution_records import execution_record_store
from app.core.local_sessions import LocalSessionStore, session_store
from app.core.storage import store
from app.skills.base import SkillContext
from app.skills.lark_cli.skill import LarkCLISkill

DEFAULT_TIMEZONE = "Asia/Shanghai"
SCHEDULED_TASK_CONFIG_KEY = "scheduled_tasks"
SCHEDULER_LEASE_KEY = "scheduled_task_runner"
SCHEDULER_LEASE_TTL_SECONDS = 300
SCHEDULER_LEASE_RETRY_SECONDS = 5
SCHEDULE_KEYWORDS = ("定时", "每天", "每日", "每逢", "工作日", "提醒我", "到点", "届时", "自动执行", "定期")
STRONG_SCHEDULE_CUES = ("定时", "每天", "每日", "每逢", "工作日", "到点", "届时", "自动执行", "定期")
MESSAGE_ACTION_RE = re.compile(
    r"(?:发送|发|通知|知会|说一声|告诉)(?:给|到|一下|一声|大家|群|消息|通知)?"
)
TIME_RE = re.compile(
    r"(?:(上午|早上|中午|下午|晚上|凌晨)\s*)?"
    r"([0-2]?\d|[一二两三四五六七八九十]{1,3})"
    r"\s*([:：点时])\s*"
    r"([0-5]?\d|半|[一二两三四五六七八九十]{1,3})?"
)
DATE_RE = re.compile(r"(20\d{2})\s*年\s*([01]?\d)\s*月\s*([0-3]?\d)\s*[日号]?")


@dataclass
class ScheduleIntent:
    original_request: str
    task_message: str
    schedule_type: str
    next_run_at: int
    time_of_day: str
    timezone: str = DEFAULT_TIMEZONE
    max_runs: int | None = None

    def to_preview(self) -> dict[str, Any]:
        run_at = datetime.fromtimestamp(self.next_run_at, ZoneInfo(self.timezone))
        return {
            "original_request": self.original_request,
            "task_message": self.task_message,
            "schedule_type": self.schedule_type,
            "time_of_day": self.time_of_day,
            "timezone": self.timezone,
            "next_run_at": self.next_run_at,
            "next_run_at_text": run_at.strftime("%Y-%m-%d %H:%M:%S"),
            "max_runs": self.max_runs,
        }


def _cn_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.startswith("十"):
        tail = value[1:]
        return 10 + digits.get(tail, 0)
    if "十" in value:
        head, tail = value.split("十", 1)
        return digits.get(head, 0) * 10 + digits.get(tail, 0)
    return digits.get(value)


def _parse_time(text: str) -> tuple[int, int, str] | None:
    match = TIME_RE.search(text)
    if not match:
        return None
    period, hour_raw, _separator, minute_raw = match.groups()
    hour = _cn_number(hour_raw)
    if hour is None or hour > 24:
        return None
    if minute_raw in {None, ""}:
        minute = 0
    elif minute_raw == "半":
        minute = 30
    else:
        minute = _cn_number(minute_raw)
        if minute is None:
            return None
    if minute > 59:
        return None

    if period in {"下午", "晚上"} and 1 <= hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif period == "凌晨" and hour == 12:
        hour = 0
    elif hour == 24 and minute == 0:
        hour = 0
    elif hour >= 24:
        return None
    return hour, minute, f"{hour:02d}:{minute:02d}"


def _message_payload_separator(text: str) -> int:
    for separator in ("：", ":"):
        index = text.find(separator)
        if index >= 0:
            return index
    return -1


def has_schedule_execution_intent(message: str) -> bool:
    """Return whether a time expression controls execution rather than content."""

    text = (message or "").strip()
    time_match = TIME_RE.search(text)
    if not text or not time_match:
        return False

    separator_index = _message_payload_separator(text)
    control_text = text[:separator_index] if separator_index >= 0 else text
    if any(cue in control_text for cue in STRONG_SCHEDULE_CUES):
        return True

    # `提醒我` is a scheduling cue only outside a message payload. A message
    # such as `给群发消息：明天提醒我带材料` must remain an immediate send.
    if "提醒我" in control_text:
        return True

    if separator_index >= 0 and time_match.start() > separator_index:
        if MESSAGE_ACTION_RE.search(control_text):
            return False

    action_match = MESSAGE_ACTION_RE.search(text)
    if action_match and action_match.start() < time_match.start():
        # In ordinary speech the words following “告诉/知会/发消息” are the
        # message content unless an explicit scheduler cue says otherwise.
        return False

    date_before_time = re.search(r"(?:今天|明天|后天|20\d{2}\s*年)", text[: time_match.start() + 1])
    action_after_time = MESSAGE_ACTION_RE.search(text, time_match.end())
    return bool(date_before_time and action_after_time)


def schedule_clarification_for_query(message: str) -> str:
    text = (message or "").strip()
    if not text or not any(cue in text for cue in ("每逢", "工作日", "每天", "每日", "定期")):
        return ""
    if TIME_RE.search(text):
        return ""
    return "请补充这个后台任务的具体执行时间（例如“每个工作日 18:00”），我再生成可确认的执行计划。"


def _strip_schedule_text(text: str) -> str:
    cleaned = text.strip()
    hour = r"(?:[0-2]?\d|[一二两三四五六七八九十]{1,3})"
    minute = r"(?:[0-5]?\d|半|[一二两三四五六七八九十]{1,3})?"
    patterns = [
        rf"(从)?(?:每逢|每个)?工作日\s*(上午|早上|中午|下午|晚上|凌晨)?\s*{hour}\s*[:：点时]?\s*{minute}\s*(开始|的时候)?",
        rf"(从)?每天\s*(上午|早上|中午|下午|晚上|凌晨)?\s*{hour}\s*[:：点时]?\s*{minute}\s*(开始|的时候)?",
        rf"(从)?每日\s*(上午|早上|中午|下午|晚上|凌晨)?\s*{hour}\s*[:：点时]?\s*{minute}\s*(开始|的时候)?",
        rf"在?\s*(明天|后天|今天)\s*(上午|早上|中午|下午|晚上|凌晨)?\s*{hour}\s*[:：点时]?\s*{minute}\s*(的时候)?",
        rf"在?\s*20\d{{2}}\s*年\s*[01]?\d\s*月\s*[0-3]?\d\s*[日号]?\s*(上午|早上|中午|下午|晚上|凌晨)?\s*{hour}\s*[:：点时]?\s*{minute}\s*(的时候)?",
        r"^\s*定时(任务)?",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, count=1)
    cleaned = re.sub(r"^(请)?帮我?", "", cleaned)
    cleaned = re.sub(r"^[，,。；;\s]+", "", cleaned)
    cleaned = cleaned.strip()
    return cleaned or text.strip()


def parse_schedule_intent(message: str, now: datetime | None = None, timezone: str = DEFAULT_TIMEZONE) -> ScheduleIntent | None:
    text = (message or "").strip()
    if not text:
        return None
    if not any(keyword in text for keyword in SCHEDULE_KEYWORDS) and not re.match(
        r"^(?:请|麻烦|帮我)?\s*(?:在|等到)?\s*(?:今天|明天|后天|20\d{2}\s*年)", text
    ):
        return None
    if not has_schedule_execution_intent(text):
        return None
    if any(word in text for word in ("会议", "日程", "参会")) and not re.search(r"定时|到点|提醒我|届时|自动执行", text):
        return None

    tz = ZoneInfo(timezone)
    current = now.astimezone(tz) if now else datetime.now(tz)
    date_match = DATE_RE.search(text)
    time_text = text
    for marker in ("每天", "每日", "今天", "明天", "后天"):
        if marker in text:
            time_text = text.split(marker, 1)[1]
            break
    if date_match:
        time_text = text[date_match.end() :]
    parsed_time = _parse_time(time_text)
    if not parsed_time:
        return None
    hour, minute, time_of_day = parsed_time

    schedule_type = ""
    max_runs: int | None = None
    if "工作日" in text:
        schedule_type = "weekdays"
        run_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if run_at <= current:
            run_at += timedelta(days=1)
        while run_at.weekday() >= 5:
            run_at += timedelta(days=1)
    elif "每天" in text or "每日" in text:
        schedule_type = "daily"
        run_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if run_at <= current:
            run_at += timedelta(days=1)
    else:
        schedule_type = "once"
        max_runs = 1
        if date_match:
            year, month, day = (int(item) for item in date_match.groups())
            run_at = datetime(year, month, day, hour, minute, tzinfo=tz)
            if run_at <= current:
                return None
        elif "后天" in text:
            target = current + timedelta(days=2)
            run_at = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elif "明天" in text:
            target = current + timedelta(days=1)
            run_at = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elif "今天" in text or "定时" in text:
            run_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_at <= current:
                return None
        else:
            return None

    task_message = _strip_schedule_text(text)
    if task_message == text and schedule_type == "daily":
        task_message = re.sub(r"(每天|每日)", "", task_message, count=1).strip(" ，,。；;") or text

    return ScheduleIntent(
        original_request=text,
        task_message=task_message,
        schedule_type=schedule_type,
        next_run_at=int(run_at.timestamp()),
        time_of_day=time_of_day,
        timezone=timezone,
        max_runs=max_runs,
    )


class ScheduledTaskStore:
    def add(self, *, user_id: str, session_id: str, intent: ScheduleIntent) -> dict[str, Any]:
        safe_user = LocalSessionStore._safe_user_id(user_id)
        now = store.now()
        task_id = store.execute_insert(
            """
            INSERT INTO scheduled_tasks(
                user_id, session_id, original_request, task_message, schedule_type,
                time_of_day, timezone, next_run_at, status, max_runs, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                safe_user,
                session_id,
                intent.original_request,
                intent.task_message,
                intent.schedule_type,
                intent.time_of_day,
                intent.timezone,
                intent.next_run_at,
                intent.max_runs,
                now,
                now,
            ),
        )
        row = store.query_one("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,))
        return self._row_to_dict(row) if row else {}

    def due_tasks(self, now_ts: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        rows = store.query_all(
            """
            SELECT * FROM scheduled_tasks
            WHERE status = 'active' AND next_run_at <= ?
            ORDER BY next_run_at ASC
            LIMIT ?
            """,
            (now_ts or store.now(), max(1, min(limit, 50))),
        )
        return [self._row_to_dict(row) for row in rows]

    def mark_running(self, task_id: int) -> bool:
        """Atomically move a task to ``running``.

        ``mark_running`` is kept for callers that only need the state change.
        The scheduler uses :meth:`claim_due`, which also persists the
        occurrence in the same transaction before any external side effect.
        """

        now = store.now()
        with store.transaction() as conn:
            cursor = conn.execute(
                "UPDATE scheduled_tasks SET status = 'running', updated_at = ? "
                "WHERE id = ? AND status = 'active'",
                (now, task_id),
            )
            return cursor.rowcount == 1

    def claim_due(
        self,
        task_id: int,
        now_ts: int | None = None,
        lease_owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim one due task and persist its occurrence.

        A random occurrence key is generated only inside the claim transaction.
        If another scheduler process wins the ``active -> running`` update, the
        loser receives ``None`` and cannot invoke the external agent.
        """

        now = now_ts or store.now()
        occurrence_key = uuid.uuid4().hex
        with store.transaction() as conn:
            if lease_owner_id is not None:
                lease = conn.execute(
                    "SELECT owner_id, expires_at FROM scheduler_leases WHERE lease_key = ?",
                    (SCHEDULER_LEASE_KEY,),
                ).fetchone()
                if not lease or lease["owner_id"] != lease_owner_id or int(lease["expires_at"]) <= now:
                    return None
            row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row or row["status"] != "active" or int(row["next_run_at"]) > now:
                return None
            cursor = conn.execute(
                "UPDATE scheduled_tasks SET status = 'running', updated_at = ? "
                "WHERE id = ? AND status = 'active' AND next_run_at <= ?",
                (now, task_id, now),
            )
            if cursor.rowcount != 1:
                return None
            run_cursor = conn.execute(
                """
                INSERT INTO scheduled_task_runs(
                    task_id, occurrence_key, scheduled_for, status, started_at
                ) VALUES (?, ?, ?, 'running', ?)
                """,
                (task_id, occurrence_key, int(row["next_run_at"]), now),
            )
            claimed = self._row_to_dict(row)
            claimed.update(
                {
                    "status": "running",
                    "scheduled_task_run_id": int(run_cursor.lastrowid),
                    "scheduled_task_occurrence_key": occurrence_key,
                }
            )
            return claimed

    def complete_run(self, task: dict[str, Any], result: dict[str, Any]) -> bool:
        return self._finish_run(task, result, success=True)

    def fail_run(self, task: dict[str, Any], result: dict[str, Any]) -> bool:
        return self._finish_run(task, result, success=False)

    def mark_unknown(self, task: dict[str, Any], message: str) -> bool:
        """Stop a run when the external side effect result is not trustworthy."""

        now = store.now()
        result = {
            "success": False,
            "status": "unknown",
            "requires_confirmation": True,
            "message": message,
        }
        run_id = task.get("scheduled_task_run_id")
        with store.transaction() as conn:
            if run_id is not None:
                run_cursor = conn.execute(
                    """
                    UPDATE scheduled_task_runs
                    SET status = 'unknown', result_json = ?, finished_at = ?
                    WHERE id = ? AND task_id = ? AND status = 'running'
                    """,
                    (store.dumps(result), now, int(run_id), int(task["id"])),
                )
                if run_cursor.rowcount != 1:
                    return False
            cursor = conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'paused', last_result_json = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (store.dumps(result), now, int(task["id"])),
            )
            return cursor.rowcount == 1

    def _finish_run(self, task: dict[str, Any], result: dict[str, Any], *, success: bool) -> bool:
        now = store.now()
        run_id = task.get("scheduled_task_run_id")
        result_json = store.dumps(result)
        with store.transaction() as conn:
            current = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task["id"],)).fetchone()
            if not current or current["status"] != "running":
                return False
            if run_id is not None:
                run = conn.execute(
                    "SELECT status FROM scheduled_task_runs WHERE id = ? AND task_id = ?",
                    (int(run_id), int(task["id"])),
                ).fetchone()
                if not run or run["status"] != "running":
                    return False

            run_count = int(current["run_count"] or 0) + 1
            max_runs = current["max_runs"]
            recurring = current["schedule_type"] in {"daily", "weekdays"}
            reached_limit = max_runs is not None and run_count >= int(max_runs)
            if recurring:
                # A recurring task remains schedulable after each successful
                # occurrence.  Only an explicit max_runs limit completes it.
                status = "completed" if reached_limit else "active"
            else:
                status = "completed" if success or reached_limit else "failed"
            next_run_at = int(current["next_run_at"])
            if recurring and status == "active":
                next_run_at = self._next_daily_run(current["time_of_day"], current["timezone"] or DEFAULT_TIMEZONE, now)
                if current["schedule_type"] == "weekdays":
                    next_run_at = self._next_weekday_run(
                        current["time_of_day"], current["timezone"] or DEFAULT_TIMEZONE, now
                    )

            task_cursor = conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = ?, run_count = ?, last_run_at = ?, next_run_at = ?,
                    last_result_json = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (status, run_count, now, next_run_at, result_json, now, int(task["id"])),
            )
            if task_cursor.rowcount != 1:
                return False
            if run_id is not None:
                run_cursor = conn.execute(
                    """
                    UPDATE scheduled_task_runs
                    SET status = ?, result_json = ?, finished_at = ?
                    WHERE id = ? AND task_id = ? AND status = 'running'
                    """,
                    ("succeeded" if success else "failed", result_json, now, int(run_id), int(task["id"])),
                )
                if run_cursor.rowcount != 1:
                    raise RuntimeError("scheduled task occurrence disappeared before completion")
            return True

    def list_for_user(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = store.query_all(
            """
            SELECT * FROM scheduled_tasks
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (LocalSessionStore._safe_user_id(user_id), max(1, min(limit, 500))),
        )
        return [self._row_to_dict(row) for row in rows]

    def recover_running(self) -> int:
        """Pause in-flight work after a restart instead of replaying it.

        A process cannot know whether a remote write happened before it died.
        Marking the occurrence ``unknown`` gives the user an explicit recovery
        point and prevents the next scheduler loop from sending it again.
        """

        now = store.now()
        recovered = 0
        unknown_result = store.dumps(
            {
                "success": False,
                "status": "unknown",
                "requires_confirmation": True,
                "message": "服务重启时该定时任务仍在执行，外部操作结果不明确，任务已暂停；确认后再恢复。",
            }
        )
        with store.transaction() as conn:
            rows = conn.execute("SELECT id FROM scheduled_tasks WHERE status = 'running'").fetchall()
            for row in rows:
                task_id = int(row["id"])
                conn.execute(
                    """
                    UPDATE scheduled_task_runs
                    SET status = 'unknown', result_json = ?, finished_at = ?
                    WHERE task_id = ? AND status = 'running'
                    """,
                    (unknown_result, now, task_id),
                )
                cursor = conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET status = 'paused', last_result_json = ?, updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (unknown_result, now, task_id),
                )
                recovered += cursor.rowcount
        return recovered

    def acquire_runner_lease(self, owner_id: str, ttl_seconds: int = SCHEDULER_LEASE_TTL_SECONDS) -> bool:
        """Acquire the single scheduler lease, or renew it for the same owner."""

        now = store.now()
        expires_at = now + max(30, int(ttl_seconds))
        with store.transaction() as conn:
            row = conn.execute(
                "SELECT owner_id, expires_at FROM scheduler_leases WHERE lease_key = ?",
                (SCHEDULER_LEASE_KEY,),
            ).fetchone()
            if row and row["owner_id"] != owner_id and int(row["expires_at"]) > now:
                return False
            if row:
                conn.execute(
                    """
                    UPDATE scheduler_leases
                    SET owner_id = ?, acquired_at = ?, heartbeat_at = ?, expires_at = ?
                    WHERE lease_key = ?
                    """,
                    (owner_id, now, now, expires_at, SCHEDULER_LEASE_KEY),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO scheduler_leases(
                        lease_key, owner_id, acquired_at, heartbeat_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (SCHEDULER_LEASE_KEY, owner_id, now, now, expires_at),
                )
            return True

    def renew_runner_lease(self, owner_id: str, ttl_seconds: int = SCHEDULER_LEASE_TTL_SECONDS) -> bool:
        now = store.now()
        expires_at = now + max(30, int(ttl_seconds))
        with store.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE scheduler_leases
                SET heartbeat_at = ?, expires_at = ?
                WHERE lease_key = ? AND owner_id = ? AND expires_at > ?
                """,
                (now, expires_at, SCHEDULER_LEASE_KEY, owner_id, now),
            )
            return cursor.rowcount == 1

    def release_runner_lease(self, owner_id: str) -> bool:
        with store.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM scheduler_leases WHERE lease_key = ? AND owner_id = ?",
                (SCHEDULER_LEASE_KEY, owner_id),
            )
            return cursor.rowcount == 1

    def runner_lease_valid(self, owner_id: str) -> bool:
        row = store.query_one(
            "SELECT owner_id, expires_at FROM scheduler_leases WHERE lease_key = ?",
            (SCHEDULER_LEASE_KEY,),
        )
        return bool(row and row["owner_id"] == owner_id and int(row["expires_at"]) > store.now())

    @staticmethod
    def _next_daily_run(time_of_day: str, timezone: str, now_ts: int) -> int:
        tz = ZoneInfo(timezone or DEFAULT_TIMEZONE)
        current = datetime.fromtimestamp(now_ts, tz)
        hour, minute = (int(item) for item in (time_of_day or "09:00").split(":", 1))
        run_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if run_at <= current:
            run_at += timedelta(days=1)
        return int(run_at.timestamp())

    @staticmethod
    def _next_weekday_run(time_of_day: str, timezone: str, now_ts: int) -> int:
        tz = ZoneInfo(timezone or DEFAULT_TIMEZONE)
        current = datetime.fromtimestamp(now_ts, tz)
        hour, minute = (int(item) for item in (time_of_day or "09:00").split(":", 1))
        run_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if run_at <= current:
            run_at += timedelta(days=1)
        while run_at.weekday() >= 5:
            run_at += timedelta(days=1)
        return int(run_at.timestamp())

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "session_id": row["session_id"],
            "original_request": row["original_request"],
            "task_message": row["task_message"],
            "schedule_type": row["schedule_type"],
            "time_of_day": row["time_of_day"],
            "timezone": row["timezone"],
            "next_run_at": row["next_run_at"],
            "last_run_at": row["last_run_at"],
            "status": row["status"],
            "run_count": row["run_count"],
            "max_runs": row["max_runs"],
            "last_result": store.loads(row["last_result_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


scheduled_task_store = ScheduledTaskStore()


def _clamp_poll_seconds(value: int | None) -> int:
    if value is None:
        return 30
    return max(5, min(int(value), 3600))


class ScheduledTaskConfigStore:
    def get(self) -> dict[str, Any]:
        settings = get_settings()
        defaults = {
            "enabled": bool(settings.SCHEDULED_TASKS_ENABLED),
            "poll_seconds": _clamp_poll_seconds(settings.SCHEDULED_TASK_POLL_SECONDS),
            "timezone": DEFAULT_TIMEZONE,
        }
        row = store.query_one("SELECT value_json, updated_at FROM system_settings WHERE key = ?", (SCHEDULED_TASK_CONFIG_KEY,))
        if not row:
            return {**defaults, "updated_at": None}
        saved = store.loads(row["value_json"], {})
        return {
            "enabled": bool(saved.get("enabled", defaults["enabled"])),
            "poll_seconds": _clamp_poll_seconds(saved.get("poll_seconds", defaults["poll_seconds"])),
            "timezone": str(saved.get("timezone") or defaults["timezone"]),
            "updated_at": row["updated_at"],
        }

    def update(self, *, enabled: bool | None = None, poll_seconds: int | None = None) -> dict[str, Any]:
        current = self.get()
        next_value = {
            "enabled": current["enabled"] if enabled is None else bool(enabled),
            "poll_seconds": current["poll_seconds"] if poll_seconds is None else _clamp_poll_seconds(poll_seconds),
            "timezone": current["timezone"],
        }
        now = store.now()
        store.execute(
            """
            INSERT INTO system_settings(key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (SCHEDULED_TASK_CONFIG_KEY, store.dumps(next_value), now),
        )
        return {**next_value, "updated_at": now}

    def enabled(self) -> bool:
        return bool(self.get()["enabled"])


scheduled_task_config_store = ScheduledTaskConfigStore()


class ScheduledTaskRunner:
    def __init__(
        self,
        interval_seconds: int = 30,
        lease_ttl_seconds: int = SCHEDULER_LEASE_TTL_SECONDS,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.lease_ttl_seconds = max(30, int(lease_ttl_seconds))
        self.owner_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._lease_held = False

    def start(self) -> bool:
        if self._task and not self._task.done():
            return self._lease_held
        self._stop_event = asyncio.Event()
        try:
            self._lease_held = self._try_acquire_lease()
        except Exception:
            self._lease_held = False
        if self._lease_held:
            # Only the process that owns the lease may classify running work
            # as abandoned after a restart.
            try:
                scheduled_task_store.recover_running()
            except Exception:
                self._lease_held = False
            else:
                self._start_heartbeat()
        self._task = asyncio.create_task(self._loop())
        return self._lease_held

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            await self._task
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        try:
            # Release even if startup acquired the lease but recovery failed
            # before `_lease_held` could remain true.  The owner predicate
            # makes this a no-op for a different worker.
            scheduled_task_store.release_runner_lease(self.owner_id)
        except Exception:
            pass
        self._lease_held = False

    async def _loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            if not self._lease_held:
                try:
                    acquired = self._try_acquire_lease()
                except Exception:
                    acquired = False
                if acquired:
                    try:
                        scheduled_task_store.recover_running()
                    except Exception:
                        self._lease_held = False
                        continue
                    self._start_heartbeat()
                    continue
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=SCHEDULER_LEASE_RETRY_SECONDS,
                    )
                except asyncio.TimeoutError:
                    pass
                continue
            await self.run_due_once()
            if not self._lease_held:
                continue
            try:
                config = scheduled_task_config_store.get()
            except Exception:
                self._lease_held = False
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=config.get("poll_seconds") or self.interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    def _try_acquire_lease(self) -> bool:
        acquired = scheduled_task_store.acquire_runner_lease(self.owner_id, self.lease_ttl_seconds)
        if acquired:
            self._lease_held = True
        return acquired

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        interval = max(1, min(self.lease_ttl_seconds // 3, 30))
        while self._stop_event is not None and not self._stop_event.is_set():
            await asyncio.sleep(interval)
            if not self._lease_held:
                return
            try:
                renewed = scheduled_task_store.renew_runner_lease(self.owner_id, self.lease_ttl_seconds)
            except Exception:
                renewed = False
            if not renewed:
                # Stop claiming new work.  A successor may now recover the
                # in-flight occurrence, so the current execution must not
                # start another one until it reacquires the lease.
                self._lease_held = False
                return

    async def run_due_once(self) -> None:
        if not self._lease_held:
            return
        try:
            if not scheduled_task_store.runner_lease_valid(self.owner_id):
                self._lease_held = False
                return
        except Exception:
            # A failed lease check is fail-closed.  The heartbeat may have
            # died on the same database error; do not trust the stale bool and
            # continue claiming external work.
            self._lease_held = False
            return
        try:
            enabled = scheduled_task_config_store.enabled()
        except Exception:
            self._lease_held = False
            return
        if not enabled:
            return
        try:
            due_tasks = scheduled_task_store.due_tasks()
        except Exception:
            self._lease_held = False
            return
        for task in due_tasks:
            if not self._lease_held:
                return
            try:
                claimed = scheduled_task_store.claim_due(int(task["id"]), lease_owner_id=self.owner_id)
            except Exception:
                self._lease_held = False
                return
            if not claimed:
                try:
                    if not scheduled_task_store.runner_lease_valid(self.owner_id):
                        self._lease_held = False
                        return
                except Exception:
                    self._lease_held = False
                    return
                continue
            await self._run_task(claimed)

    async def _run_task(self, task: dict[str, Any]) -> None:
        from app.core.account_access import account_execution_error

        access_error = account_execution_error(task["user_id"])
        if access_error:
            scheduled_task_store.fail_run(task, {"success": False, "message": access_error})
            return
        try:
            skill = LarkCLISkill()
            session = session_store.get_or_create(task["user_id"], task["session_id"])
            context = SkillContext(
                session_id=task["session_id"],
                user_id=task["user_id"],
                message=task["task_message"],
                history=session_store.history_for_context(session),
                metadata={
                    "scheduled_task_id": task["id"],
                    "scheduled_task_run_id": task.get("scheduled_task_run_id"),
                    "scheduled_task_occurrence_key": task.get("scheduled_task_occurrence_key"),
                    "account_name": task["user_id"],
                },
            )
            result = await skill.execute(context, query=task["task_message"], confirm_write=True)
            payload = {
                "success": result.success,
                "message": result.message,
                "data": result.data or {},
            }
            run_state = (result.data or {}).get("run_state")
            if not run_state:
                run_state = ((result.data or {}).get("plan") or {}).get("run_state")
            ambiguous_result = run_state == "service_error" or any(
                isinstance(record, dict) and record.get("status") == "unknown"
                for record in (result.data or {}).get("executed_commands", [])
            )
        except Exception as exc:
            # An exception can happen after a remote write but before the
            # result is persisted locally.  Retrying would risk a duplicate.
            scheduled_task_store.mark_unknown(task, f"定时任务执行结果不明确：{exc}")
            return

        try:
            session_store.append_message(task["user_id"], task["session_id"], "user", f"[定时任务] {task['task_message']}")
            session_store.append_message(task["user_id"], task["session_id"], "assistant", result.message, result.data or {})
            execution_record_store.add(
                user_id=task["user_id"],
                session_id=task["session_id"],
                request=task["task_message"],
                plan={
                    **((result.data or {}).get("plan") or {}),
                    "scheduled_task_id": task["id"],
                    "scheduled_task_run_id": task.get("scheduled_task_run_id"),
                    "scheduled_task_occurrence_key": task.get("scheduled_task_occurrence_key"),
                },
                executed_commands=(result.data or {}).get("executed_commands") or [],
                success=result.success,
            )
            finished = (
                scheduled_task_store.mark_unknown(
                    task,
                    f"定时任务执行服务异常，外部操作结果不明确：{result.message}",
                )
                if ambiguous_result
                else (
                    scheduled_task_store.complete_run(task, payload)
                    if result.success
                    else scheduled_task_store.fail_run(task, payload)
                )
            )
            if not finished:
                scheduled_task_store.mark_unknown(task, "定时任务状态落盘失败，外部操作结果不明确。")
        except Exception as exc:
            # Local bookkeeping is part of the recovery boundary.  Do not
            # convert a persistence failure into a retryable task failure.
            scheduled_task_store.mark_unknown(task, f"定时任务结果保存失败，外部操作结果不明确：{exc}")


scheduled_task_runner = ScheduledTaskRunner()
