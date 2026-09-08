from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.routes.auth import AccountInfo, get_current_account
from app.core.workflow_memory import workflow_memory_store

router = APIRouter()


@router.get("/workflow-memories")
async def list_workflow_memories(
    limit: int = Query(100, ge=1, le=200),
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    return {"code": 0, "data": workflow_memory_store.list_public_for_user(account.account, limit)}


@router.post("/workflow-memories/{memory_id}/activate")
async def activate_workflow_memory(
    memory_id: int,
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    memory = workflow_memory_store.activate(account.account, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Workflow memory not found")
    return {"code": 0, "data": workflow_memory_store.public_view(memory)}


@router.delete("/workflow-memories/{memory_id}")
async def disable_workflow_memory(
    memory_id: int,
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    if not workflow_memory_store.disable(account.account, memory_id):
        raise HTTPException(status_code=404, detail="Workflow memory not found")
    return {"code": 0, "message": "disabled"}
