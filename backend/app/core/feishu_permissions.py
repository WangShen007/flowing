from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.storage import store


@dataclass(frozen=True)
class Capability:
    title: str
    scopes: tuple[str, ...]
    lead_only: bool = False


@dataclass(frozen=True)
class ScenarioAccess:
    ready: bool
    capabilities: tuple[str, ...]
    reason: str = ""


# User-identity scopes verified against larksuite/cli shortcut definitions.
# Native API variants use the same narrow scopes as their corresponding shortcuts.
CAPABILITIES = {
    "documents_read": Capability("读取和搜索文档", ("docx:document:readonly", "search:docs:read")),
    "documents_write": Capability("创建和编辑文档", ("docx:document:create", "docx:document:write_only")),
    "documents_import": Capability("导入文件", ("docs:document.media:upload", "docs:document:import")),
    "groups_read": Capability("查看本人所在群组", ("im:chat:read",)),
    "group_members_read": Capability("查看群成员", ("im:chat.members:read",)),
    "messages_read": Capability("查询消息", ("search:message", "im:message.reactions:read",
        "im:message.group_msg:get_as_user", "im:message.p2p_msg:get_as_user")),
    "messages_send": Capability("以本人身份发送消息", ("im:message.send_as_user", "im:message")),
    "people_search": Capability("查找同事", ("contact:user:search",)),
    "calendar_read": Capability("查看日程", ("calendar:calendar.event:read",)),
    "calendar_availability": Capability("查询共同空闲时间和会议室", ("calendar:calendar.free_busy:read",)),
    "calendar_write": Capability("创建和更新日程", ("calendar:calendar.event:create", "calendar:calendar.event:update")),
    "calendar_rsvp": Capability("回复日程邀请", ("calendar:calendar.event:reply",)),
    "tasks_read": Capability("查看任务", ("task:task:read",)),
    "tasks_write": Capability("创建和更新任务", ("task:task:write",)),
    "meetings_read": Capability("查询会议", ("vc:meeting.search:read",)),
    "meetings_detail_read": Capability("读取视频会议详情", ("vc:meeting.meetingevent:read", "vc:record:readonly")),
    "minutes_search": Capability("搜索妙记", ("minutes:minutes.search:read",)),
    "minutes_read": Capability("读取妙记及总结待办", ("minutes:minutes.basic:read", "minutes:minutes.artifacts:read")),
    "base_read": Capability("查看多维表格", ("base:table:read", "base:field:read", "base:record:read", "base:view:read")),
    "base_create": Capability("创建多维表格", ("base:app:create", "base:table:read", "base:table:create", "base:table:update", "base:table:delete")),
    "base_structure": Capability("创建多维表格数据表", ("base:table:create", "base:field:read", "base:field:create", "base:field:update", "base:view:write_only")),
    "base_table_write": Capability("修改多维表格名称", ("base:table:update",)),
    "base_fields_write": Capability("创建多维表格字段", ("base:field:create",)),
    "base_records_write": Capability("新增和更新多维表格记录", ("base:record:create", "base:record:update")),
    "slides_write": Capability("创建演示文稿", ("slides:presentation:create", "slides:presentation:write_only", "docs:document.media:upload")),
    "groups_create": Capability("创建项目群组", ("im:chat:create_by_user",), lead_only=True),
    "tasklists_write": Capability("管理项目任务清单", ("task:tasklist:write", "task:task:write"), lead_only=True),
}

COMMANDS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("docs", "+fetch"): ("documents_read",),
    ("docs", "+search"): ("documents_read",),
    ("docs", "+create"): ("documents_write",),
    ("docs", "+update"): ("documents_read", "documents_write"),
    ("drive", "+import"): ("documents_import",),
    ("drive", "+task_result"): (),
    ("im", "+chat-list"): ("groups_read",),
    ("im", "+chat-search"): ("groups_read",),
    ("im", "chats", "get"): ("groups_read",),
    ("im", "+chat-members-list"): ("group_members_read",),
    ("im", "chat.members", "get"): ("group_members_read",),
    ("im", "+chat-messages-list"): ("messages_read",),
    ("im", "+messages-search"): ("messages_read",),
    ("im", "+messages-send"): ("messages_send",),
    ("im", "+chat-create"): ("groups_create",),
    ("contact", "+search-user"): ("people_search",),
    ("calendar", "+agenda"): ("calendar_read",),
    ("calendar", "+get"): ("calendar_read",),
    ("calendar", "+search-event"): ("calendar_read",),
    ("calendar", "+list-attendees"): ("calendar_read",),
    ("calendar", "+meeting"): ("calendar_read",),
    ("calendar", "+rsvp"): ("calendar_rsvp",),
    ("calendar", "+freebusy"): ("calendar_availability",),
    ("calendar", "+room-find"): ("calendar_availability",),
    ("calendar", "+suggestion"): ("calendar_availability",),
    ("calendar", "+create"): ("calendar_write",),
    ("calendar", "+update"): ("calendar_write",),
    ("calendar", "events", "create"): ("calendar_write",),
    ("calendar", "events", "list"): ("calendar_read",),
    ("calendar", "events", "get"): ("calendar_read",),
    ("calendar", "event.attendees", "create"): ("calendar_write",),
    ("task", "+get-my-tasks"): ("tasks_read",),
    ("task", "+search"): ("tasks_read",),
    ("task", "+update"): ("tasks_read", "tasks_write"),
    ("task", "+complete"): ("tasks_read", "tasks_write"),
    ("task", "+assign"): ("tasks_read", "tasks_write"),
    ("task", "+tasklist-create"): ("tasklists_write",),
    ("task", "tasks", "create"): ("tasks_write",),
    ("task", "tasks", "get"): ("tasks_read",),
    ("task", "tasks", "delete"): ("tasks_read", "tasks_write"),
    ("vc", "+search"): ("meetings_read",),
    ("vc", "+detail"): ("meetings_detail_read",),
    ("minutes", "+search"): ("minutes_search",),
    ("minutes", "+detail"): ("minutes_read",),
    ("base", "+base-create"): ("base_create",),
    ("base", "+table-create"): ("base_structure",),
    ("base", "+table-list"): ("base_read",),
    ("base", "+table-get"): ("base_read",),
    ("base", "+table-update"): ("base_table_write",),
    ("base", "+field-list"): ("base_read",),
    ("base", "+field-get"): ("base_read",),
    ("base", "+field-create"): ("base_fields_write",),
    ("base", "+record-list"): ("base_read",),
    ("base", "+record-search"): ("base_read",),
    ("base", "+record-get"): ("base_read",),
    ("base", "+record-batch-create"): ("base_records_write",),
    ("base", "+record-batch-update"): ("base_records_write",),
    ("base", "+record-upsert"): ("base_records_write",),
    ("slides", "+create"): ("slides_write",),
}

# A role bundle describes onboarding; an individual operation can need less.
COMMAND_SCOPES: dict[tuple[str, ...], tuple[str, ...]] = {
    ("base", "+table-list"): ("base:table:read",),
    ("base", "+field-list"): ("base:field:read",),
    ("base", "+record-list"): ("base:record:read",),
    ("base", "+record-search"): ("base:record:read",),
    ("base", "+record-get"): ("base:record:read",),
    ("base", "+record-batch-create"): ("base:record:create",),
    ("base", "+record-batch-update"): ("base:record:update",),
    ("docs", "+fetch"): ("docx:document:readonly",),
    ("docs", "+search"): ("search:docs:read",),
    ("docs", "+create"): ("docx:document:create",),
    ("docs", "+update"): ("docx:document:readonly", "docx:document:write_only"),
    ("im", "+messages-search"): ("search:message", "im:message.reactions:read"),
    ("im", "+chat-messages-list"): ("im:message.group_msg:get_as_user", "im:message.p2p_msg:get_as_user", "im:message.reactions:read"),
    ("calendar", "+update"): ("calendar:calendar.event:update",),
    ("calendar", "+rsvp"): ("calendar:calendar.event:reply",),
    ("calendar", "events", "create"): ("calendar:calendar.event:create",),
    ("calendar", "event.attendees", "create"): ("calendar:calendar.event:update",),
    ("base", "+table-get"): ("base:table:read", "base:field:read", "base:view:read"),
    ("base", "+table-update"): ("base:table:update",),
    ("base", "+field-get"): ("base:field:read",),
    ("base", "+field-create"): ("base:field:create",),
    ("vc", "+detail"): ("vc:meeting.meetingevent:read", "vc:record:readonly"),
}

# Official CLI v1.0.93 conditional dependencies (local document media / wiki
# targets). Include them in onboarding and explicit operation authorization,
# without blocking a plain-text operation that does not use those features.
CONDITIONAL_COMMAND_SCOPES: dict[tuple[str, ...], tuple[str, ...]] = {
    ("docs", "+create"): ("docs:document.media:upload", "docx:document:write_only", "docx:document:readonly"),
    ("docs", "+update"): ("docs:document.media:upload", "wiki:node:retrieve"),
    ("drive", "+import"): ("wiki:node:retrieve",),
}


# Built-in templates are enabled in enterprise mode only after every operation they
# can perform has a reviewed command and user-scope mapping. Keeping the unreviewed
# set explicit makes newly added templates fail the coverage test instead of
# silently becoming available.
SCENARIO_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "send_group_notice": ("groups_read", "messages_send"),
    "schedule_meeting": ("people_search", "calendar_availability", "calendar_write"),
    "create_doc": ("documents_write",),
    "base_import": ("documents_import",),
    "meeting_summary": ("meetings_read", "minutes_search", "minutes_read"),
    "minutes_action_items_to_tasks": (
        "minutes_read", "people_search", "tasks_write", "groups_read", "messages_send",
    ),
    "recommend_group_meeting_times": (
        "groups_read", "messages_read", "calendar_availability", "calendar_write", "groups_create",
    ),
    "minutes_to_doc": ("minutes_read", "documents_write"),
    "book_meeting_room": ("calendar_availability", "calendar_write", "people_search", "messages_send"),
    "search_minutes_by_keyword": ("minutes_search", "minutes_read"),
    "send_group_message_as_me": ("groups_read", "messages_send"),
    "send_direct_message_as_me": ("people_search", "messages_send"),
    "markdown_to_doc": ("documents_write",),
    "repeated_explanations_to_docs": ("minutes_search", "minutes_read", "documents_write"),
    "search_docs_and_messages": ("documents_read", "messages_read"),
    "create_lark_slides": ("slides_write",),
    "today_calendar_task_plan": ("calendar_read", "tasks_read"),
    "today_due_task_summary": ("tasks_read",),
}

SCENARIO_UNREVIEWED = frozenset({
    "weekly_minutes_decision_dashboard",
    "meeting_id_to_summary_media",
    "thread_summary",
    "scan_unread_mentions",
    "review_doc_with_comments",
    "create_board_architecture",
    "share_doc_permissions",
    "doc_image_attachment_ops",
    "monitor_doc_comments",
    "draft_followup_email_with_images",
    "unread_email_triage",
    "send_email_with_recall_window",
    "monitor_new_emails",
    "business_email_with_signature",
    "add_subtasks_comment_reminder",
    "create_project_task_tree",
    "subscribe_task_events",
    "sheet_find_rows_analyze",
    "base_form_automation",
    "base_upload_attachment",
    "base_role_permissions",
    "archive_group_files_to_drive",
    "drive_large_file_cleanup",
    "wiki_manage_nodes",
    "approval_batch_handle",
    "subscribe_lark_events",
    "approval_status_followup",
    "recommend_skills_from_activity",
    "update_memory_from_activity",
    "create_lark_skill",
    "openapi_explorer_call",
    "query_resource_fields_table",
})


def required_command_scopes(args: list[str], capabilities: tuple[str, ...]) -> set[str]:
    if args[1:3] == ["minutes", "+detail"]:
        scopes = {"minutes:minutes.basic:read"}
        if any(item.split("=", 1)[0] in {"--summary", "--todo", "--chapter", "--transcript", "--keyword"}
               and not item.endswith("=false") for item in args[3:]):
            scopes.add("minutes:minutes.artifacts:read")
        return scopes
    for command, scopes in COMMAND_SCOPES.items():
        if tuple(args[1:1 + len(command)]) == command:
            return set(scopes)
    return {scope for key in capabilities for scope in CAPABILITIES[key].scopes}


def capabilities_for_role(role: str) -> list[str]:
    if role not in {"employee", "lead", "admin"}:
        raise ValueError("未知网站角色，拒绝授权，请联系管理员")
    return [key for key, value in CAPABILITIES.items() if not value.lead_only or role in {"lead", "admin"}]


def scenario_access(template_id: str, role: str) -> ScenarioAccess:
    capabilities = SCENARIO_CAPABILITIES.get(template_id)
    if capabilities is None:
        return ScenarioAccess(
            ready=False,
            capabilities=(),
            reason="该模板包含尚未纳入企业安全清单的飞书操作，暂不可在企业模式执行。",
        )
    allowed = set(capabilities_for_role(role))
    unavailable = [key for key in capabilities if key not in allowed]
    if unavailable:
        titles = "、".join(CAPABILITIES[key].title for key in unavailable)
        return ScenarioAccess(
            ready=False,
            capabilities=capabilities,
            reason=f"当前角色不能使用此模板所需的能力：{titles}。请联系管理员调整角色。",
        )
    return ScenarioAccess(ready=True, capabilities=capabilities)


def scopes_for_role(role: str) -> list[str]:
    allowed = set(capabilities_for_role(role))
    scopes = {"offline_access", *(scope for key in allowed for scope in CAPABILITIES[key].scopes)}
    for command, conditional in CONDITIONAL_COMMAND_SCOPES.items():
        if set(COMMANDS[command]) <= allowed:
            scopes.update(conditional)
    return sorted(scopes)


def member_role(account: str) -> str:
    row = store.query_one(
        """SELECT COALESCE(m.role, 'employee') AS role FROM accounts a
           LEFT JOIN account_memberships m ON m.account = a.account
           WHERE a.account = ? AND COALESCE(m.enabled, 1) = 1""", (account,),
    )
    if not row:
        raise ValueError("账号已停用或不存在")
    return row["role"]


def command_capabilities(args: list[str]) -> tuple[str, ...]:
    if not args or args[0] != "lark-cli":
        raise ValueError("仅允许企业飞书命令")
    path = args[1:]
    if path == ["--help"] or (len(path) == 2 and path[-1] == "--help" and path[0] in {key[0] for key in COMMANDS}):
        return ()
    if path and path[0] == "schema" and 2 <= len(path) <= 4 and all(
        item.replace(".", "").replace("_", "").isalnum() for item in path[1:]
    ):
        return ()
    for command, capabilities in COMMANDS.items():
        if tuple(path[:len(command)]) == command:
            if len(path) > len(command) and not path[len(command)].startswith("--"):
                raise ValueError("命令参数不符合企业执行规范")
            return capabilities
    raise ValueError("此操作尚未纳入企业能力清单，请联系管理员")


def check_command_permission(account: str, args: list[str], *, authorization_only: bool = False) -> list[str]:
    role = member_role(account)
    requested = command_capabilities(args)
    if any(key not in capabilities_for_role(role) for key in requested):
        raise ValueError("当前角色不能执行此操作，请联系管理员调整角色")
    if not authorization_only and args[1:4] == ["im", "chat.members", "get"]:
        # The raw endpoint exposes a security-check switch. Never let a model
        # or an explicit CLI request opt out, including via repeated JSON flags.
        parameter_values = []
        for index, argument in enumerate(args):
            if argument == "--params":
                parameter_values.append(args[index + 1] if index + 1 < len(args) else "")
            elif argument.startswith("--params="):
                parameter_values.append(argument.split("=", 1)[1])
        if not parameter_values:
            raise ValueError("群成员查询必须启用安全检查 check_security_conf=true")
        for value in parameter_values:
            try:
                params = json.loads(value)
            except (ValueError, TypeError) as exc:
                raise ValueError("群成员查询参数必须为内联 JSON 对象") from exc
            if not isinstance(params, dict) or params.get("check_security_conf") is not True:
                raise ValueError("群成员查询必须启用安全检查 check_security_conf=true")
    for index, argument in enumerate(args):
        if argument == "--as" and (index + 1 >= len(args) or args[index + 1] != "user"):
            raise ValueError("企业任务只能以当前用户身份执行")
        if argument.startswith("--as=") and argument != "--as=user":
            raise ValueError("企业任务只能以当前用户身份执行")
        if argument.split("=", 1)[0] in {"--profile", "--config", "--app-id", "--app-secret"}:
            raise ValueError("任务不能切换飞书应用或账号")
    grant = store.query_one("SELECT scopes FROM feishu_grants WHERE account = ? AND revoked = 0", (account,))
    granted = set(grant["scopes"].split()) if grant else set()
    required = required_command_scopes(args, requested)
    if authorization_only:
        for command, conditional in CONDITIONAL_COMMAND_SCOPES.items():
            if tuple(args[1:1 + len(command)]) == command:
                required.update(conditional)
    return sorted(required - granted)
