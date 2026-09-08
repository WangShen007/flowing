# Scenario Test Results - 2026-09-07 to 2026-09-08

Environment: local application http://127.0.0.1:8000, admin account,
configured DeepSeek model deepseek-v4-flash, isolated Feishu user profile.
No credentials are included in this report.

## Coverage

| Scenario | Actual verification | Result |
| --- | --- | --- |
| All 50 built-in templates | Live render API, filled values, every required field individually omitted | Passed; this does not verify external operations |
| Generate document | Rendered template scheduled through chat; task 4 naturally triggered at 22:05 Asia/Shanghai, completed once; independent CLI fetch | Passed; document Em8YdR5OeoUGyWxIehjc1Lqmnxf, both body lines present |
| Markdown to document | Actual project chat execution followed by independent CLI fetch | Passed after fix; document BoywdX0huo8p7MxUPg4cYZhRnIe; h1, list, Python code indentation, and table present |
| Scheduled title/paragraph regression | Task 5 paused during fix, resumed, automatically dispatched by background runner; independent CLI fetch | Passed; completed once; document ZWh4d32ahoImnmxK1XocV8hSnMh retains title and two paragraphs |
| CSV to Base | Incremental consent, actual import of design/scenario-test-data.csv, then independent table and record reads | Passed; Base Gj49bEtSAa66QRsEb1Wcj6etntb contains both populated CSV rows plus eight blank rows created by the platform |
| Create and read Base | Actual Base creation followed by table and record reads using the server-held user grant | Passed; Base Anw6bMMIfa4PORsWoOmcIAg2nhe |
| Meeting summary | Real missing-scope checkpoint, incremental consent, automatic continuation and readable empty-result response | Passed for the read-only authorization-continuation path; no summary with matching content was available |
| All built-in templates in enterprise mode | Explicit policy coverage for all 50 templates | 18 reviewed templates enabled for admin/lead, 32 unreviewed templates disabled before render/execution; employee has 17 enabled because project-group creation is lead-only |
| Remaining external operations | Schema/render validation or explicit enterprise denial | Not verified end to end; disabled until their commands and user scopes are reviewed |

The initial Markdown execution failed because command normalization truncated
the command at the first newline. After repair, the same content succeeded.
The initial scheduled document also exposed removal of the word used for
"scheduled" from a document title; task 5 verifies the repaired title handling.

## Repairs

- Exclude the exact application-generated scenario wrapper from deterministic
  parsing and write-intent classification; actual write commands still require
  confirmation.
- Recognize corner-quoted document and Base names and the document content
  prefix used by the built-in template.
- Preserve Markdown paragraphs and indentation through query normalization,
  scheduled-task storage, command quoting, and command normalization.
- Reject unquoted newline command separators while allowing quoted multiline
  arguments.
- Route requests with enabled AI expansion through model planning instead of
  immediately creating a document from the short outline.
- Restrict removal of the scheduling keyword to the request prefix.

## 2026-09-08 Base authorization follow-up

The test application received the following additive user-identity permissions;
existing permissions were not removed:

```text
base:app:create
base:field:create
base:field:read
base:field:update
base:record:create
base:record:read
base:record:update
base:table:create
base:table:delete
base:table:read
base:table:update
base:view:write_only
```

The Feishu console reported the changes published. Incremental OAuth displayed
only these missing business permissions while retaining continuous access. The
resulting administrator role bundle contains 40 unique scopes including
`offline_access`; the existing grant contains all required scopes and a refresh
credential. No credential value is recorded here.

The CSV readback returned `scenario-test-a / ready` and
`scenario-test-b / done`. The eight additional blank rows are Base defaults and
were intentionally left untouched because cleanup was not authorized.

## Validation And Retained Resources

- Backend pytest: 127 passed, with one upstream Starlette/AnyIO deprecation warning.
- Focused Ruff checks, frontend TypeScript/Vite build, all 13 browser-harness
  groups and `git diff --check` passed.
- Real Chrome displayed 32 `企业未开放` badges. Selecting one showed its
  fail-closed reason and disabled all form controls. The corresponding render
  API returned HTTP 403 before a prompt or command was produced.
- Tasks 4 and 5 remain visible as completed, each run_count=1. Scheduler test
  task 7 completed once. At this checkpoint, task 6 remained active for
  2026-09-08 15:00 Asia/Shanghai and had not been modified. It later reached its
  due time and was safely marked failed before CLI execution because its attendee
  fields were placeholders; see `WORKFLOW-RELIABILITY.md`.
- Three synthetic test documents remain accessible for inspection. No messages
  were sent to other people and no meeting invitations were issued.
- Twelve additive Base permissions were granted during the 2026-09-08 follow-up.
  No messages, invitations or destructive cleanup operations were performed.
- Long-running reliability, daily recurrence, and service downtime recovery were
  not tested by these once-only runs.

Document links:

- https://ucnn92h1qs1c.feishu.cn/docx/Em8YdR5OeoUGyWxIehjc1Lqmnxf
- https://ucnn92h1qs1c.feishu.cn/docx/BoywdX0huo8p7MxUPg4cYZhRnIe
- https://ucnn92h1qs1c.feishu.cn/docx/ZWh4d32ahoImnmxK1XocV8hSnMh
- https://ucnn92h1qs1c.feishu.cn/base/Anw6bMMIfa4PORsWoOmcIAg2nhe
- https://ucnn92h1qs1c.feishu.cn/base/Gj49bEtSAa66QRsEb1Wcj6etntb
