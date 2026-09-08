from __future__ import annotations

from time import perf_counter
from typing import Any

from app.skills.base import SkillContext


async def build_plan_preview(skill: Any, context: SkillContext, query: str) -> dict[str, Any]:
    """Build a dry-run plan without executing Lark CLI commands."""

    started_at = perf_counter()
    original_query = skill._sanitize_query_text(query or context.message)
    cli_state = await skill._probe_cli_state(context.user_id, context.metadata.get("account_name", ""))
    plan, selected_skills, references = await skill._plan_commands(
        context,
        original_query,
        cli_state,
        normalize_intent=False,
        llm_timeout=skill._plan_llm_timeout,
    )
    commands = []
    for item in plan.get("commands", [])[:10]:
        command = skill._normalize_command(str(item.get("command") or ""))
        if not command:
            continue
        commands.append(
            {
                "command": command,
                "reason": item.get("reason", ""),
                "expected": skill._normalize_expected_type(item.get("expected")),
                # Label the individual command by its own side effect. The
                # overall request is considered separately below so a read-only
                # target lookup can still precede a confirmed send operation.
                "write": skill._is_write_request("", [command]),
            }
        )

    need_confirmation = False
    if not plan.get("requires_input"):
        need_confirmation = bool(plan.get("need_confirmation")) or skill._is_write_request(
            plan.get("normalized_query") or original_query,
            [item["command"] for item in commands],
        )
    return {
        "query": original_query,
        "summary": plan.get("summary") or "",
        "normalized_query": plan.get("normalized_query", ""),
        "intent_type": plan.get("intent_type", ""),
        "confidence": plan.get("confidence"),
        "requires_input": bool(plan.get("requires_input")),
        "corrections": plan.get("corrections") or [],
        "workflow_memory": plan.get("workflow_memory"),
        "relevant_skills": plan.get("relevant_skills") or [doc.key for doc in selected_skills],
        "references": plan.get("references") or [ref.path.name for ref in references],
        "reason_for_confirmation": plan.get("reason_for_confirmation") or (
            "This request may write to Feishu and should be confirmed before execution."
            if need_confirmation
            else ""
        ),
        "need_confirmation": need_confirmation,
        "commands": commands,
        "planning_source": plan.get("planning_source", "fallback"),
        "planning_duration_ms": max(0, round((perf_counter() - started_at) * 1000)),
        "cli_state": {
            "installed": cli_state.installed,
            "configured": cli_state.configured,
            "authenticated": cli_state.authenticated,
            "ready": cli_state.ready,
            "profile": cli_state.profile,
        },
    }
