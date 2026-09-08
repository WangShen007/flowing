# Enterprise scenario policy

This document is an auditable snapshot, not the executable source of truth. The
source of truth is `backend/app/core/feishu_permissions.py`; the coverage test in
`backend/tests/test_enterprise_scenarios.py` requires every built-in template to
be either reviewed or explicitly unreviewed.

## Capability to user-scope mapping

| Capability | User scopes |
| --- | --- |
| `documents_read` | `docx:document:readonly`, `search:docs:read` |
| `documents_write` | `docx:document:create`, `docx:document:write_only` |
| `documents_import` | `docs:document.media:upload`, `docs:document:import` |
| `groups_read` | `im:chat:read` |
| `messages_read` | `search:message`, `im:message.reactions:read`, `im:message.group_msg:get_as_user`, `im:message.p2p_msg:get_as_user` |
| `messages_send` | `im:message.send_as_user` |
| `people_search` | `contact:user:search` |
| `calendar_read` | `calendar:calendar.event:read` |
| `calendar_availability` | `calendar:calendar.free_busy:read` |
| `calendar_write` | `calendar:calendar.event:create`, `calendar:calendar.event:update` |
| `tasks_read` | `task:task:read` |
| `tasks_write` | `task:task:write` |
| `meetings_read` | `vc:meeting.search:read` |
| `minutes_search` | `minutes:minutes.search:read` |
| `minutes_read` | `minutes:minutes.basic:read`, `minutes:minutes.artifacts:read` |
| `base_read` | `base:table:read`, `base:field:read`, `base:record:read` |
| `base_create` | `base:app:create`, `base:table:read`, `base:table:create`, `base:table:update`, `base:table:delete` |
| `base_structure` | `base:table:create`, `base:field:read`, `base:field:create`, `base:field:update`, `base:view:write_only` |
| `base_records_write` | `base:record:create`, `base:record:update` |
| `slides_write` | `slides:presentation:create`, `slides:presentation:write_only`, `docs:document.media:upload` |
| `groups_create` | `im:chat:create_by_user` (lead/admin only) |
| `tasklists_write` | `task:tasklist:write`, `task:task:write` (lead/admin only) |

`offline_access` is always included in onboarding and incremental consent so a
replacement grant keeps a refresh credential. Individual commands can request a
narrower subset than the capability bundle; see `COMMAND_SCOPES` in the source.

## Reviewed built-in templates

| Template id | Required capabilities |
| --- | --- |
| `send_group_notice` | `groups_read`, `messages_send` |
| `schedule_meeting` | `people_search`, `calendar_availability`, `calendar_write` |
| `create_doc` | `documents_write` |
| `base_import` | `documents_import` |
| `meeting_summary` | `meetings_read`, `minutes_search`, `minutes_read` |
| `minutes_action_items_to_tasks` | `minutes_read`, `people_search`, `tasks_write`, `groups_read`, `messages_send` |
| `recommend_group_meeting_times` | `groups_read`, `messages_read`, `calendar_availability`, `calendar_write`, `groups_create` |
| `minutes_to_doc` | `minutes_read`, `documents_write` |
| `book_meeting_room` | `calendar_availability`, `calendar_write`, `people_search`, `messages_send` |
| `search_minutes_by_keyword` | `minutes_search`, `minutes_read` |
| `send_group_message_as_me` | `groups_read`, `messages_send` |
| `send_direct_message_as_me` | `people_search`, `messages_send` |
| `markdown_to_doc` | `documents_write` |
| `repeated_explanations_to_docs` | `minutes_search`, `minutes_read`, `documents_write` |
| `search_docs_and_messages` | `documents_read`, `messages_read` |
| `create_lark_slides` | `slides_write` |
| `today_calendar_task_plan` | `calendar_read`, `tasks_read` |
| `today_due_task_summary` | `tasks_read` |

These 18 templates are available to lead/admin. Employees have 17 because
`recommend_group_meeting_times` can create a project group. Reviewed means every
possible operation in the template has an enforced command/scope mapping; it is
not a claim that every external operation has passed a live end-to-end test.

## Explicitly unreviewed built-in templates

The following 32 IDs are disabled in enterprise mode before template render:

```text
weekly_minutes_decision_dashboard
meeting_id_to_summary_media
thread_summary
scan_unread_mentions
review_doc_with_comments
create_board_architecture
share_doc_permissions
doc_image_attachment_ops
monitor_doc_comments
draft_followup_email_with_images
unread_email_triage
send_email_with_recall_window
monitor_new_emails
business_email_with_signature
add_subtasks_comment_reminder
create_project_task_tree
subscribe_task_events
sheet_find_rows_analyze
base_form_automation
base_upload_attachment
base_role_permissions
archive_group_files_to_drive
drive_large_file_cleanup
wiki_manage_nodes
approval_batch_handle
subscribe_lark_events
approval_status_followup
recommend_skills_from_activity
update_memory_from_activity
create_lark_skill
openapi_explorer_call
query_resource_fields_table
```

To enable one, review every command variant against the installed CLI and official
user-scope documentation, add command and scope mappings, move its ID to
`SCENARIO_CAPABILITIES`, and add focused tests. The executor continues to reject
unknown commands even after a template is marked reviewed.
