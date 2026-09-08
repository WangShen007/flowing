import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import AccountInfo, get_current_account
from app.config import get_settings
from app.core.execution_records import execution_record_store
from app.core.local_sessions import session_store
from app.core.scheduled_tasks import (
    parse_schedule_intent,
    schedule_clarification_for_query,
    scheduled_task_config_store,
    scheduled_task_store,
)
from app.core.workflow_checkpoints import claim_checkpoint, save_checkpoint
from app.core.workflow_memory import workflow_memory_store
from app.skills.ai_ppt import AIPPTSkill, is_ai_ppt_request
from app.skills.base import SkillContext, SkillResult
from app.skills.lark_cli.skill import LarkCLISkill

router = APIRouter()
logger = logging.getLogger(__name__)
_active_runs: dict[tuple[str, str], asyncio.Task] = {}
_active_sessions: set[tuple[str, str]] = set()
_settings = get_settings()
# Preserve an outer cleanup window around the preview-only model budget. Even
# if operators configure the API value too low, the timeout hierarchy remains
# valid: model attempt < API request < browser network safety deadline.
PLAN_PREVIEW_TIMEOUT = max(
    _settings.LARK_CLI_PLAN_TIMEOUT,
    _settings.LARK_CLI_PLAN_LLM_TIMEOUT + 2,
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32000)
    user_id: str = "local"
    session_id: str = Field(default="", max_length=128)
    skill: str = Field(default="auto", max_length=80)
    command: str = Field(default="", max_length=16000)
    confirm_write: bool = False
    confirm_plan: bool = False
    timeout: int | None = None
    stream: bool = True
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=80)
    resume_id: str = Field(default="", max_length=80)


@router.post("/chat/{run_id}/cancel")
async def cancel_chat(run_id: str, account: AccountInfo = Depends(get_current_account)) -> dict[str, Any]:
    task = _active_runs.get((account.account, run_id))
    if task and not task.done() and not task.cancelling():
        task.cancel()
        await asyncio.wait({task}, timeout=3)
    return {"code": 0, "data": {"cancelled": bool(task)}}


def _attach_checkpoint(metadata: dict[str, Any], request: ChatRequest, context: SkillContext) -> None:
    if metadata.get("setup_required") or metadata.get("approval_required") or (
        metadata.get("agent_run_id") and metadata.get("run_state") == "service_error"
    ):
        metadata["resume_id"] = save_checkpoint(context.user_id, context.session_id, {
            "query": request.message,
            "command": request.command,
            "confirm_write": request.confirm_write,
            "confirm_plan": request.confirm_plan,
            "skill": request.skill,
            "plan": metadata.get("plan") or {},
            "executed_commands": metadata.get("executed_commands") or [],
            "agent_run_id": metadata.get("agent_run_id"),
            "approval_required": bool(metadata.get("approval_required")),
        })
        metadata["resume_query"] = request.message
        if metadata.get("run_state") != "service_error":
            metadata["run_state"] = "waiting_approval" if metadata.get("approval_required") else "waiting_authorization"


class PlanPreviewRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32000)
    user_id: str = "local"
    session_id: str = Field(default="", max_length=128)
    skill: str = Field(default="auto", max_length=80)


def _serialize_sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _result_payload(result: SkillResult) -> dict[str, Any]:
    return {
        "type": "final",
        "success": result.success,
        "content": result.message,
        "metadata": result.data or {},
        "need_continue": result.need_continue,
    }


def _record_execution_and_memory(
    *,
    user_id: str,
    session_id: str,
    request: str,
    plan: dict[str, Any],
    executed_commands: list[dict[str, Any]],
    success: bool,
) -> dict[str, Any] | None:
    record_id = execution_record_store.add(
        user_id=user_id,
        session_id=session_id,
        request=request,
        plan=plan,
        executed_commands=executed_commands,
        success=success,
    )
    memory_reference = plan.get("workflow_memory") if isinstance(plan, dict) else None
    if success:
        memory = workflow_memory_store.record_success(
            user_id=user_id,
            execution_record_id=record_id,
            request=request,
            plan=plan,
            executed_commands=executed_commands,
        )
        return workflow_memory_store.public_view(memory) if memory else None
    attempted_failure = bool(executed_commands) and any(
        not (item.get("success") or item.get("superseded_by_repair"))
        for item in executed_commands
        if isinstance(item, dict)
    )
    if attempted_failure and isinstance(memory_reference, dict) and memory_reference.get("id"):
        workflow_memory_store.record_failure(user_id, int(memory_reference["id"]))
    return None


def _scheduled_task_message(task: dict[str, Any]) -> str:
    next_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(task["next_run_at"])))
    label = {"daily": "每天", "weekdays": "工作日"}.get(task.get("schedule_type"), "一次性")
    return (
        f"已创建{label}定时任务。\n\n"
        f"- 任务 ID：{task['id']}\n"
        f"- 执行内容：{task['task_message']}\n"
        f"- 下次执行：{next_text}（{task.get('timezone') or 'Asia/Shanghai'}）\n\n"
        "到达时间后，系统会自动执行该飞书任务，并把执行结果写入当前会话和执行记录。"
    )


def _schedule_confirmation_message(intent: Any) -> str:
    preview = intent.to_preview()
    trigger_label = {"daily": "每天重复", "weekdays": "每个工作日"}.get(
        preview["schedule_type"], "一次性"
    )
    return (
        "检测到这是一个定时任务，请先确认后再创建：\n\n"
        f"- 执行内容：{preview['task_message']}\n"
        f"- 触发类型：{trigger_label}\n"
        f"- 下次执行：{preview['next_run_at_text']}（{preview['timezone']}）\n\n"
        "如果确认创建，请通过执行计划预览点击“确认执行”。"
    )


def _schedule_disabled_message() -> str:
    return "定时任务当前已关闭。请先在输入框上方的「定时任务」面板打开开关，再创建新的定时任务。"


def _ai_ppt_has_feishu_followup(message: str) -> bool:
    text = (message or "").lower()
    return any(
        keyword in text
        for keyword in (
            "发给",
            "发送",
            "转发",
            "群",
            "群里",
            "私聊",
            "上传",
            "保存",
            "云空间",
            "云文档",
            "drive",
            "chat",
            "send",
            "share",
            "upload",
        )
    )


@router.post("/chat")
async def chat(request: ChatRequest, account: AccountInfo = Depends(get_current_account)):
    settings = get_settings()
    request.session_id = request.session_id or str(uuid.uuid4())
    session_key = (account.account, request.session_id)
    run_key = (account.account, request.run_id)
    if session_key in _active_sessions or run_key in _active_runs:
        raise HTTPException(status_code=409, detail="此会话仍在执行，请先停止当前任务。")
    if (len(_active_sessions) >= settings.CHAT_MAX_ACTIVE_PER_PROCESS
            or sum(user == account.account for user, _ in _active_sessions) >= settings.CHAT_MAX_ACTIVE_PER_ACCOUNT):
        raise HTTPException(status_code=429, detail="当前执行任务较多，请稍后重试；本次未开始执行。", headers={"Retry-After": "3"})
    # No await between checking and reserving: atomic on this ASGI event loop.
    # This bounds one worker, not a distributed rate limiter.
    _active_sessions.add(session_key)
    _active_runs[run_key] = asyncio.current_task()

    def release() -> None:
        _active_sessions.discard(session_key)
        _active_runs.pop(run_key, None)

    streaming = False
    try:
        result = await _chat_impl(request, account)
        if isinstance(result, StreamingResponse):
            iterator = result.body_iterator

            async def guarded_stream():
                try:
                    async for chunk in iterator:
                        yield chunk
                finally:
                    release()

            result.body_iterator = guarded_stream()
            streaming = True
        return result
    finally:
        if not streaming:
            release()


async def _chat_impl(request: ChatRequest, account: AccountInfo):
    settings = get_settings()
    resolved_user_id = account.account
    checkpoint = None
    approve_tool = False
    if request.resume_id:
        checkpoint = claim_checkpoint(request.resume_id, resolved_user_id, request.session_id)
        if checkpoint is None:
            raise HTTPException(status_code=409, detail="此任务已恢复或不属于当前会话，请查看最新执行结果。")
        approve_tool = bool(checkpoint.get("approval_required") and request.confirm_write)
        request.message = checkpoint["query"]
        request.command = checkpoint.get("command", "")
        request.confirm_write = bool(checkpoint.get("confirm_write"))
        request.confirm_plan = bool(checkpoint.get("confirm_plan"))
        request.skill = checkpoint.get("skill", "lark_cli")
    skill = AIPPTSkill() if is_ai_ppt_request(request.message, request.skill) else LarkCLISkill()
    session = session_store.get_or_create(resolved_user_id, request.session_id)
    session_id = str(session["session_id"])
    context = SkillContext(
        session_id=session_id,
        user_id=resolved_user_id,
        message=request.message,
        history=session_store.history_for_context(session),
        metadata={"created_at": int(time.time()), "account_name": account.name},
    )
    timeout = request.timeout or settings.LARK_CLI_COMMAND_TIMEOUT
    if checkpoint:
        context.metadata["resume_checkpoint"] = checkpoint
        context.metadata["approve_tool"] = approve_tool
    legacy_schedule = settings.LARK_AGENT_ENGINE != "langgraph"
    schedule_clarification = schedule_clarification_for_query(request.message) if legacy_schedule else None
    schedule_intent = parse_schedule_intent(request.message) if legacy_schedule else None
    schedule_enabled = scheduled_task_config_store.enabled()

    if not request.stream:
        if schedule_clarification:
            metadata = {"requires_input": True, "schedule_ambiguous": True}
            session_store.append_message(context.user_id, context.session_id, "user", request.message)
            session_store.append_message(
                context.user_id, context.session_id, "assistant", schedule_clarification, metadata
            )
            return {
                "code": 0,
                "data": {
                    "type": "final",
                    "success": False,
                    "content": schedule_clarification,
                    "metadata": metadata,
                },
            }
        if schedule_intent and not schedule_enabled:
            message = _schedule_disabled_message()
            metadata = {"schedule_disabled": True, "schedule": schedule_intent.to_preview()}
            session_store.append_message(context.user_id, context.session_id, "user", request.message)
            session_store.append_message(context.user_id, context.session_id, "assistant", message, metadata)
            return {"code": 0, "data": {"type": "final", "success": False, "content": message, "metadata": metadata}}

        if schedule_intent and not request.confirm_plan:
            message = _schedule_confirmation_message(schedule_intent)
            metadata = {"schedule_required": True, "schedule": schedule_intent.to_preview()}
            session_store.append_message(context.user_id, context.session_id, "user", request.message)
            session_store.append_message(context.user_id, context.session_id, "assistant", message, metadata)
            return {"code": 0, "data": {"type": "final", "success": False, "content": message, "metadata": metadata}}

        if schedule_intent and request.confirm_plan:
            task = scheduled_task_store.add(user_id=context.user_id, session_id=context.session_id, intent=schedule_intent)
            message = _scheduled_task_message(task)
            metadata = {"scheduled_task_created": True, "scheduled_task": task}
            schedule_plan = {
                "type": "scheduled_task",
                "intent_type": "scheduled_task",
                "schedule": schedule_intent.to_preview(),
                "need_confirmation": True,
            }
            memory = _record_execution_and_memory(
                user_id=context.user_id,
                session_id=context.session_id,
                request=request.message,
                plan=schedule_plan,
                executed_commands=[],
                success=True,
            )
            if memory:
                metadata["workflow_memory"] = memory
            session_store.append_message(context.user_id, context.session_id, "user", request.message)
            session_store.append_message(context.user_id, context.session_id, "assistant", message, metadata)
            return {"code": 0, "data": {"type": "final", "success": True, "content": message, "metadata": metadata}}

        result = await skill.execute(
            context,
            query=request.message,
            command=request.command or None,
            confirm_write=request.confirm_write or (isinstance(skill, AIPPTSkill) and request.confirm_plan),
            timeout=timeout,
        )
        if result.data is None:
            result.data = {}
        _attach_checkpoint(result.data, request, context)
        plan = result.data.get("plan") or {}
        executed_commands = result.data.get("executed_commands") or []
        memory = _record_execution_and_memory(
            user_id=context.user_id,
            session_id=context.session_id,
            request=request.message,
            plan=plan,
            executed_commands=executed_commands,
            success=result.success,
        )
        if memory:
            result.data["workflow_memory"] = memory
        session_store.append_message(context.user_id, context.session_id, "user", request.message)
        session_store.append_message(
            context.user_id,
            context.session_id,
            "assistant",
            result.message,
            result.data or {},
        )
        return {"code": 0, "data": _result_payload(result)}

    async def event_stream() -> AsyncGenerator[str, None]:
        full_response = ""
        final_metadata: dict[str, Any] = {}
        progress_events: list[str] = []
        run_key = (context.user_id, request.run_id)
        _active_runs[run_key] = asyncio.current_task()
        saved = False
        session_store.append_message(context.user_id, context.session_id, "user", request.message)

        def save_response() -> None:
            nonlocal saved
            if not saved:
                session_store.append_message(context.user_id, context.session_id, "assistant", full_response, final_metadata)
                saved = True

        try:
            yield _serialize_sse({"type": "session", "session_id": context.session_id})
            if schedule_clarification:
                full_response = schedule_clarification
                final_metadata = {"requires_input": True, "schedule_ambiguous": True}
                save_response()
                yield _serialize_sse({"type": "metadata", "metadata": final_metadata})
                yield _serialize_sse({"type": "content", "content": full_response})
                yield _serialize_sse({"type": "done", "session_id": context.session_id})
                return
            if schedule_intent and not schedule_enabled:
                full_response = _schedule_disabled_message()
                final_metadata = {"schedule_disabled": True, "schedule": schedule_intent.to_preview()}
                save_response()
                yield _serialize_sse({"type": "metadata", "metadata": final_metadata})
                yield _serialize_sse({"type": "content", "content": full_response})
                yield _serialize_sse({"type": "done", "session_id": context.session_id})
                return
            if schedule_intent and request.confirm_plan:
                task = scheduled_task_store.add(user_id=context.user_id, session_id=context.session_id, intent=schedule_intent)
                full_response = _scheduled_task_message(task)
                final_metadata = {"scheduled_task_created": True, "scheduled_task": task}
                schedule_plan = {
                    "type": "scheduled_task",
                    "intent_type": "scheduled_task",
                    "schedule": schedule_intent.to_preview(),
                    "need_confirmation": True,
                }
                memory = _record_execution_and_memory(
                    user_id=context.user_id,
                    session_id=context.session_id,
                    request=request.message,
                    plan=schedule_plan,
                    executed_commands=[],
                    success=True,
                )
                if memory:
                    final_metadata["workflow_memory"] = memory
                save_response()
                yield _serialize_sse({"type": "metadata", "metadata": final_metadata})
                yield _serialize_sse({"type": "content", "content": full_response})
                yield _serialize_sse({"type": "done", "session_id": context.session_id})
                return
            if schedule_intent and not request.confirm_plan:
                full_response = _schedule_confirmation_message(schedule_intent)
                final_metadata = {"schedule_required": True, "schedule": schedule_intent.to_preview()}
                save_response()
                yield _serialize_sse({"type": "metadata", "metadata": final_metadata})
                yield _serialize_sse({"type": "content", "content": full_response})
                yield _serialize_sse({"type": "done", "session_id": context.session_id})
                return
            async for event in skill.execute_stream(
                context,
                query=request.message,
                command=request.command or None,
                confirm_write=request.confirm_write or (isinstance(skill, AIPPTSkill) and request.confirm_plan),
                timeout=timeout,
            ):
                if event.get("type") == "content":
                    content = event.get("content", "")
                    full_response += str(content or "")
                    yield _serialize_sse({"type": "content", "content": content})
                elif event.get("type") == "metadata":
                    metadata = event.get("data") or {}
                    _attach_checkpoint(metadata, request, context)
                    final_metadata.update(metadata)
                    yield _serialize_sse({"type": "metadata", "metadata": metadata})
                else:
                    content = str(event.get("content") or "")
                    if content:
                        progress_events.append(content)
                    yield _serialize_sse({"type": "progress", "content": content})
            if progress_events:
                final_metadata["lark_progress"] = progress_events
                final_metadata["execution_trace"] = progress_events
            execution_success = bool(final_metadata.get("ai_ppt")) or (
                bool(final_metadata.get("executed_commands"))
                and all(
                    item.get("success") or item.get("superseded_by_repair")
                    or ((final_metadata.get("plan") or {}).get("planning_source") == "langgraph"
                        and final_metadata.get("run_state") == "completed" and item.get("expected") == "read")
                    for item in final_metadata["executed_commands"]
                )
                and not final_metadata.get("setup_required")
                and not final_metadata.get("approval_required")
                and not final_metadata.get("requires_input")
                and ((final_metadata.get("plan") or {}).get("planning_source") != "langgraph"
                     or final_metadata.get("run_state") == "completed")
            )
            memory = _record_execution_and_memory(
                user_id=context.user_id,
                session_id=context.session_id,
                request=request.message,
                plan=final_metadata.get("plan") or {},
                executed_commands=final_metadata.get("executed_commands") or [],
                success=execution_success,
            )
            if memory:
                final_metadata["workflow_memory"] = memory
                yield _serialize_sse({"type": "metadata", "metadata": {"workflow_memory": memory}})
            save_response()
            yield _serialize_sse({"type": "done", "session_id": context.session_id})
        except asyncio.CancelledError:
            if not saved:
                full_response = (full_response + "\n\n" if full_response else "") + "已停止后续执行。已提交到飞书的操作可能已生效，可查看执行详情核对。"
                final_metadata.update({"run_state": "cancelled", "execution_trace": progress_events})
                save_response()
                execution_record_store.add(
                    user_id=context.user_id, session_id=context.session_id, request=request.message,
                    plan=final_metadata.get("plan") or {"run_state": "cancelled"},
                    executed_commands=final_metadata.get("executed_commands") or [], success=False,
                )
            return
        except Exception:
            logger.exception("Chat workflow failed")
            full_response = "请求处理失败，请重试。已执行步骤可在执行详情中查看。"
            final_metadata.update({"run_state": "failed", "execution_trace": progress_events})
            _record_execution_and_memory(
                user_id=context.user_id,
                session_id=context.session_id,
                request=request.message,
                plan=final_metadata.get("plan") or {"run_state": "failed"},
                executed_commands=final_metadata.get("executed_commands") or [],
                success=False,
            )
            save_response()
            yield _serialize_sse(
                {
                    "type": "error",
                    "content": full_response,
                }
            )
        finally:
            _active_runs.pop(run_key, None)
            _active_sessions.discard((context.user_id, context.session_id))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/plan")
async def preview_chat_plan(
    request: PlanPreviewRequest,
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    started_at = time.perf_counter()
    resolved_user_id = account.account
    session = session_store.get_session(resolved_user_id, request.session_id) if request.session_id else None
    context = SkillContext(
        session_id=str(session["session_id"]) if session else "",
        user_id=resolved_user_id,
        message=request.message,
        history=session_store.history_for_context(session) if session else [],
        metadata={"created_at": int(time.time()), "account_name": account.name},
    )

    def build_response(plan: dict[str, Any]) -> dict[str, Any]:
        plan.setdefault("planning_source", "deterministic")
        plan.setdefault("planning_duration_ms", max(0, round((time.perf_counter() - started_at) * 1000)))
        logger.info(
            "Plan preview completed account=%s source=%s duration_ms=%s commands=%s",
            account.account,
            plan["planning_source"],
            plan["planning_duration_ms"],
            len(plan.get("commands") or []),
        )
        return {"code": 0, "data": {"session_id": context.session_id, "plan": plan}}

    skill = LarkCLISkill()
    clarification = (skill.clarification_for_query(request.message) or schedule_clarification_for_query(request.message)
                     if get_settings().LARK_AGENT_ENGINE != "langgraph" else None)
    if clarification:
        return build_response({
            "summary": clarification, "requires_input": True, "commands": [],
            "relevant_skills": [], "references": [], "need_confirmation": False,
        })
    if is_ai_ppt_request(request.message, request.skill):
        slide_match = re.search(r"(\d{1,2})\s*(?:页|张|p|P|slide|slides)", request.message)
        slide_count = int(slide_match.group(1)) if slide_match else 6
        plan = {
            "query": request.message,
            "summary": (
                f"通过飞书 CLI 的演示文稿增强能力生成/修改可编辑 PPTX，预计 {max(3, min(18, slide_count))} 页；"
                "生成后可在 PPT 卡片中完整预览，并手动选择上传云文档、发到群或发给同事。"
            ),
            "normalized_query": request.message,
            "intent_type": "lark_slides_ppt",
            "relevant_skills": ["lark-cli", "lark-slides"],
            "references": ["ppt-master templates", "飞书 Slides 工作流"],
            "reason_for_confirmation": "",
            "need_confirmation": False,
            "commands": [],
            "cli_state": {},
        }
        return build_response(plan)
    schedule_intent = parse_schedule_intent(request.message) if get_settings().LARK_AGENT_ENGINE != "langgraph" else None
    if schedule_intent:
        plan = {"query": request.message, "relevant_skills": [], "references": [], "commands": []}
        schedule = schedule_intent.to_preview()
        schedule_enabled = scheduled_task_config_store.enabled()
        if not schedule_enabled:
            plan.update(
                {
                    "summary": "定时任务当前已关闭，无法创建新的后台任务。",
                    "intent_type": "scheduled_task",
                    "need_confirmation": False,
                    "reason_for_confirmation": "请先在「定时任务」面板打开全局开关。",
                    "schedule": schedule,
                    "commands": [],
                    "schedule_disabled": True,
                }
            )
            return build_response(plan)
        plan.update(
            {
                "summary": f"创建定时任务：{schedule['task_message']}，下次执行时间 {schedule['next_run_at_text']}",
                "intent_type": "scheduled_task",
                "need_confirmation": True,
                "reason_for_confirmation": "该请求会创建一个后台定时任务，到点后自动执行飞书操作，需要先确认。",
                "schedule": schedule,
                "commands": [
                    {
                        "command": "scheduled-task:create",
                        "reason": "将用户请求保存为定时任务，由后台调度器按计划执行。",
                        "expected": "scheduled_task",
                        "write": True,
                    }
                ],
            }
        )
    else:
        try:
            plan = await asyncio.wait_for(skill.preview_plan(context, request.message), timeout=PLAN_PREVIEW_TIMEOUT)
        except TimeoutError:
            logger.warning(
                "Plan preview exceeded API timeout account=%s timeout=%ss",
                account.account,
                PLAN_PREVIEW_TIMEOUT,
            )
            raise HTTPException(
                status_code=504,
                detail=f"生成计划超过{PLAN_PREVIEW_TIMEOUT:g}秒，已安全停止；本次指令尚未执行。请重试或修改指令。",
            ) from None
    return build_response(plan)


@router.get("/sessions")
async def list_sessions(
    user_id: str = Query("local"),
    limit: int = Query(50, ge=1, le=200),
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    return {"code": 0, "data": session_store.list_sessions(account.account, limit)}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user_id: str = Query("local"),
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    session = session_store.get_session(account.account, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"code": 0, "data": session}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    user_id: str = Query("local"),
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    session = session_store.get_session(account.account, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"code": 0, "data": {"messages": session.get("messages", [])}}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_id: str = Query("local"),
    account: AccountInfo = Depends(get_current_account),
) -> dict[str, Any]:
    if not session_store.delete_session(account.account, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"code": 0, "message": "Session deleted"}
