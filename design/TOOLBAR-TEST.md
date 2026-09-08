# Toolbar Verification

Date: 2026-09-07. Server: http://127.0.0.1:8000.

## Entry Points

| Entry | Actual behavior | Evidence |
| --- | --- | --- |
| Auto | Routes ordinary requests to Lark CLI; text-based PPT detection also exists | Real document read succeeded after confirmation |
| Feishu CLI | Currently uses the same backend routing as Auto | Same document read succeeded; no distinct routing behavior established |
| Scenarios | Loads saved/built-in workflows, validates fields, renders a request into the composer | 50 built-ins loaded; missing title/content displayed inline; filled document values substituted and panel closed |
| Scheduled tasks | Manages a server-side SQLite scheduler | Real-time task completed once; config, pause, resume and deletion checks passed |
| Model configuration | Persists provider endpoint, model and key in local configuration | DeepSeek selected correctly; switching presets updated draft fields; saving with blank key retained the existing key |

## Real Scheduler Run

- Created a once-only task through `/api/v1/chat`, scheduled for 21:47 Asia/Shanghai.
- Task read only the previously created test document `Kdqvd2pNboqlmRxdeXrcD4N4nJb`.
- The running server triggered the task on its normal 30-second polling loop. No manual due-date edits or direct runner invocation were used.
- It completed at 21:48:27, with `run_count=1` and `success=true`. Returned document title and three paragraphs matched revision 3.
- A second task scheduled for the following day verified active deletion rejection (409), pause (200), resume (200), pause again, then delete (200).
- Global disable blocked new schedule creation. Poll interval 35 saved successfully; interval 1 was rejected with 422.
- Original configuration restored: enabled=true, poll_seconds=30. Both test tasks were deleted. Their ordinary chat/execution audit history remains.

## Fixes

- Added a DeepSeek preset and infer the visible preset from the loaded endpoint.
- Removed the ineffective one-click Qwen button. Provider changes use the normal preset selection and save flow.
- Display model save errors instead of leaving a rejected request unhandled.
- Allow deletion of completed/failed tasks as well as paused tasks; active/running deletion remains blocked.
- Label active schedules as waiting to execute rather than already running.
- Ignore a standalone negative clause such as '不修改' when detecting write intent. Actual write commands and affirmative write requests still require confirmation; regression tests cover both cases. The UI's explicit plan review remains.

## Validation and Limits

- Backend suite: 12 passed; one upstream AnyIO deprecation warning.
- Vue/TypeScript production build passed; targeted lint and diff whitespace checks passed.
- Deterministic design audit: zero findings.
- Browser verified DeepSeek preset selection, draft provider switching, save acknowledgement, scenario validation/fill, and scheduler state display.
- Screenshot: `/tmp/feishu-toolbar-model-verified.png`.
- This does not verify all 50 scenario operations or all provider APIs. Real API calls used DeepSeek and authorized document reading only. Scheduled execution requires the backend to remain running and is subject to polling and model latency.
