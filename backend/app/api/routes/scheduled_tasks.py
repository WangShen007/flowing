from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.routes.auth import AccountInfo, get_current_account, get_admin_account
from app.core.local_sessions import LocalSessionStore
from app.core.scheduled_tasks import scheduled_task_config_store, scheduled_task_store
from app.core.storage import store

router = APIRouter()


class ScheduledTaskConfigRequest(BaseModel):
    enabled: bool | None = None
    poll_seconds: int | None = Field(default=None, ge=5, le=3600)


class ScheduledTaskResumeRequest(BaseModel):
    # A paused task may be paused because its previous remote side effect was
    # unknown.  Resuming that task must be an explicit user acknowledgement;
    # an empty body remains compatible for ordinary, deliberately paused work.
    confirm_unknown: bool = False


@router.get("/scheduled-tasks/config")
async def get_scheduled_task_config(
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    return {"code": 0, "data": scheduled_task_config_store.get()}


@router.post("/scheduled-tasks/config")
async def update_scheduled_task_config(
    request: ScheduledTaskConfigRequest,
    account: AccountInfo = Depends(get_admin_account),
) -> dict[str, Any]:
    return {
        "code": 0,
        "data": scheduled_task_config_store.update(
            enabled=request.enabled,
            poll_seconds=request.poll_seconds,
        ),
    }


@router.get("/scheduled-tasks")
async def list_scheduled_tasks(
    limit: int = Query(200, ge=1, le=500),
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    return {"code": 0, "data": scheduled_task_store.list_for_user(account.account, limit)}


@router.post("/scheduled-tasks/{task_id}/pause")
async def pause_scheduled_task(
    task_id: int,
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    owner = LocalSessionStore._safe_user_id(account.account)
    now = store.now()
    with store.transaction() as conn:
        row = conn.execute("SELECT user_id, status FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row or row["user_id"] != owner:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        if row["status"] != "active":
            raise HTTPException(status_code=409, detail="Only active scheduled tasks can be closed")
        cursor = conn.execute(
            "UPDATE scheduled_tasks SET status = 'paused', updated_at = ? "
            "WHERE id = ? AND user_id = ? AND status = 'active'",
            (now, task_id, owner),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="Scheduled task changed while closing; please retry")
    return {"code": 0, "message": "paused"}


@router.post("/scheduled-tasks/{task_id}/resume")
async def resume_scheduled_task(
    task_id: int,
    request: ScheduledTaskResumeRequest | None = None,
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    owner = LocalSessionStore._safe_user_id(account.account)
    now = store.now()
    with store.transaction() as conn:
        row = conn.execute(
            "SELECT user_id, status, last_result_json FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row or row["user_id"] != owner:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        if row["status"] != "paused":
            raise HTTPException(status_code=409, detail="Only paused scheduled tasks can be resumed")
        last_result = store.loads(row["last_result_json"], {})
        if last_result.get("status") == "unknown" and not (request and request.confirm_unknown):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "scheduled_task_result_unknown",
                    "message": "上次执行结果不明确。请确认飞书中的实际结果后，再明确确认并恢复任务。",
                    "requires_confirmation": True,
                },
            )
        if last_result.get("status") == "unknown":
            last_result = {
                **last_result,
                "status": "recovery_acknowledged",
                "previous_status": "unknown",
                "requires_confirmation": False,
                "message": "已确认核实上次执行结果，等待重新执行。",
                "recovery_acknowledged": True,
                "recovery_acknowledged_by": account.account,
                "recovery_acknowledged_at": now,
            }
        cursor = conn.execute(
            "UPDATE scheduled_tasks SET status = 'active', last_result_json = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND status = 'paused'",
            (store.dumps(last_result), now, task_id, owner),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="Scheduled task changed while resuming; please retry")
    return {"code": 0, "message": "active"}


@router.delete("/scheduled-tasks/{task_id}")
async def delete_scheduled_task(
    task_id: int,
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    owner = LocalSessionStore._safe_user_id(account.account)
    with store.transaction() as conn:
        row = conn.execute("SELECT user_id, status FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row or row["user_id"] != owner:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        if row["status"] not in {"paused", "completed", "failed"}:
            raise HTTPException(status_code=409, detail="Please close the scheduled task before deleting it")
        cursor = conn.execute(
            "DELETE FROM scheduled_tasks WHERE id = ? AND user_id = ? "
            "AND status IN ('paused', 'completed', 'failed')",
            (task_id, owner),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="Scheduled task changed while deleting; please retry")
    return {"code": 0, "message": "deleted"}
