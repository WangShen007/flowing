# Improvement verification log — 2026-09-08

Overall status: IN PROGRESS; not a claim of complete enterprise acceptance.

## Real Feishu messaging

User explicitly allowed testing in existing groups. Selected one existing group,
`大三资料`, and used the website's `LarkCLISkill.execute_command` with the currently
bound `admin` website account and **user identity**. No bot identity or scope bypass.

- Fresh group search resolved the unique expected group.
- Sent exactly this content with no mentions:
  `【网站自动化测试 20260908-A】消息发送与重复提交去重验证，请忽略，无需回复。`
- Submitted the identical command a second time using the same CLI idempotency key.
- Both API responses returned the same message ID:
  `om_x100b66cb140750b4de22efeed170ef9`.
- Read back the latest 10 group messages with reaction enrichment disabled.
  Exactly one matching test marker was present; message ID and content matched.
  Sender type was `user`.
- One test message remains in the group. No coworker was mentioned or invited.

This verifies the real backend/CLI/user-token send and read-back path, and duplicate
submission with this key. It is not a complete UI/Agent end-to-end test, not a proof
of unlimited-duration exactly-once delivery, and not a multi-account ACL test.

## Real model, synthetic Feishu

Runner: `backend/evals/natural_language.py --live-model`.
Uses a temporary database, synthetic users and groups, and a non-network business
executor. Only model calls and local CLI help/schema inspection are real.

Initial three cases exercised typo interpretation, history-based group references
with a mention/send confirmation, and duplicate group-name clarification. Initial
times were 8.22 / 32.47 / 7.68 seconds. All completed their broad expected outcomes,
but this is not enough to establish arbitrary-task accuracy.

The fixture was subsequently made more faithful: group search no longer supplies
member counts; the model must retrieve group details. A later answer incorrectly
called 12 users plus one bot “13 people”; the evaluator was tightened and the
system's field semantics clarified. The stricter suite subsequently passed all
three cases: 8.29 / 23.13 / 12.82 seconds. The initial operation catalog is now
provided in context, avoiding a compulsory discovery round. These are individual
samples, not latency percentiles or proof of arbitrary-task accuracy. Post-message
payload fidelity was independently checked against the installed CLI. The model
post wrapper is now normalized to the API's direct language-object format, and
the fake sender rejects invalid payloads, unapproved writes, unknown mention IDs
and missing/reused-with-changed-content idempotency keys. A further real-model
mention case passed in 11.31 seconds with one approval and one synthetic send.

## App scope verification

The developer console now shows these four additional **user identity** scopes
enabled, with all changes published: `base:view:read`,
`calendar:calendar.event:reply`, `vc:meeting.meetingevent:read`,
`vc:record:readonly`. Each displays access limited to the user's own permissions.
This is app-side verification only; existing users still need incremental OAuth
consent before their old tokens can exercise the new scopes.

## Robustness follow-up

- Fixed lossy account normalization in the shared local identity function:
  distinct `a.b`/`a-b`, `---`/`local`, and Chinese accounts no longer alias.
  SQLite receives the exact account through bound parameters. Missing identities
  fail closed instead of falling back to `local`.
- Added an 8-worker / 24-account synthetic concurrent message isolation test.
  This is not production load acceptance or a real multi-role Feishu ACL test.
- Existing legacy data created for non-canonical accounts is not automatically
  reassigned: any deployment that used such accounts requires an ownership audit
  before migration. Do not guess ownership of previously merged rows.
- Acknowledging an unknown scheduler result now clears the pending-verification
  flag while retaining its previous status and the actor/time audit fields.
  Pausing and resuming again no longer asks for the same acknowledgement.
- Bounded chat/preview input to 32,000 characters and direct commands to 16,000;
  oversized values are rejected before model execution or database persistence.
- Focused identity, request-boundary and scheduler regression: 31 passed.
- Removed two redundant full-history reads from each message append (three down
  to one, preserving the existing returned-session API). Session listing now
  counts messages for the selected sessions rather than grouping the entire
  user's joined message history. Indexes already cover these query predicates.
- Concurrent creation of the same session is idempotent; the insert handles only
  the exact session/account key conflict. Added a deterministic two-thread race test.
- Integrated backend suite including the final two session tests: 285 passed
  in 12.52 seconds, one upstream Starlette deprecation warning.
- Independent review found AI PPT owner-isolation gaps; remediation is in progress.
  Backend service restart and final integrated acceptance remain outstanding.

## Automated checks

- Login/security changes: salted hashes, legacy migration, explicit bootstrap,
  throttle expiry, concurrent limiter, management-script session revocation.
- HTTP security: explicit CORS origins, unknown-origin rejection, no-store auth
  responses and baseline anti-framing/content-sniffing headers.
- Agent changes: active model/tool timing records persist across approval resumes;
  server-generated message idempotency keys remain stable on resume; failed intent
  deduplication must ignore server-generated per-call keys.
- Frontend agent reported build and mocked browser regression success for safe
  Markdown, desktop/mobile layout and popup-blocked OAuth continuation. Readable
  approvals and unknown-schedule recovery UI are still being integrated.
- Scheduler initially had a recurring-success regression found during parent review;
  it was corrected and dedicated daily/weekday tests added. Final consolidated suite
  must be rerun after all parallel edits settle.

## Partial-completion and uncertain-write follow-up

- A started/unknown receipt restored at a pending write now produces an explicit
  unsuccessful write record, rather than only an ephemeral tool error. Earlier
  successful reads therefore cannot permit an incorrect completed outcome.
- A write executor exception is recorded as unknown and is not automatically
  replayed. Read exceptions retain the resumable infrastructure-failure path.
- CLI subprocess timeout/exception now carries an explicit unknown-result marker
  only after an approved write process has started. Pre-launch failures and reads
  do not receive that marker. Three isolated process-boundary tests cover this
  distinction; no real subprocess or external write is invoked by those tests.
- Scheduler treats an unknown executed-command record as ambiguous even when
  the overall graph outcome is `failed`, pausing the task for reconciliation.
- Regression coverage includes a persisted started receipt, an actual injected
  executor timeout, and a two-write flow requiring separate approvals without
  replaying the first write. These are isolated tests, not real Feishu failures.
- Latest integrated backend run: **297 passed, 1 upstream warning in 13.78s**.
  Frontend/file-isolation work is still being finalized; this does not establish
  deployment readiness or complete real Feishu acceptance.

## Context and memory budget follow-up

- Optional active memories are capped at 6,000 serialized characters per request;
  disabled/candidate memories are excluded. Oversized hints are skipped, not cut
  into a misleading partial procedure.
- Saved experience is passed as explicitly labelled reference data at user-message
  priority, no longer concatenated into the system instruction. It does not grant
  permissions or authorize replay; operation policies still run independently.
- Recent history keeps a contiguous suffix of complete messages up to 32,000
  characters / 12 messages. Omitted history is explicitly signalled; the current
  request remains separate and unmodified. This is a character bound, not a token
  estimator or a latency guarantee. Accumulated within-run tool output still needs
  a separate compaction strategy for very long workflows.
- Context-budget and existing graph regression: 25 passed.
- Integrated backend suite: 300 passed, one upstream warning, 13.52 seconds.
- Real-model typo case rerun after this change: passed in 7.05 seconds, two
  synthetic reads, no write or approval. Correctly distinguished 12 humans from
  one bot. One timing sample is not a performance percentile.

## Admission and concurrency follow-up

- Streaming and non-streaming `/chat` now share admission before checkpoint
  consumption or execution. A pending session/run is rejected with 409.
- Configurable per-ASGI-worker active limits default to 16 overall / 2 per account.
  Excess requests receive 429 plus Retry-After; they are not silently queued or
  replayed. This is not a distributed limiter; deployment must account for worker
  count and still requires multi-process chat admission for horizontal scaling.
- Reservations are released on non-stream exceptions and streaming finalization;
  tests cover duplicate blocking, account/global capacity and error cleanup.
- Integrated backend suite after the initial admission tests: 303 passed, one
  upstream warning in 13.80 seconds. Two additional focused capacity/cleanup tests
  were added afterwards; final consolidated rerun remains required.
- Running port 8000 is still the pre-update service (baseline headers unchanged).
  No restart has been performed while frontend integration is in progress.
- Later integrated run while new Bitable tests were arriving: 306 passed / 3 failed.
  The failures are new role-parametrized ACL tests expecting two calls, while the
  scripted flow also performs a legitimate read-back after the rejected write.
  The test author is correcting the assertion to verify no extra write, unchanged
  data and a failed outcome. Do not treat this run as a clean release gate.
- File-isolation agent reports owner-bound storage and authenticated frontend Blob
  loading implemented; browser scripts now use real calendar data/time shapes.
  Its build passed, but current CDP endpoint is unavailable, so fresh browser
  execution remains pending. Historical unowned PPTs remain inaccessible.

## Integrated browser and isolated-server verification

- Consolidated backend rerun: **309 passed, 1 upstream warning in 15.02s**.
  The new Bitable ACL assertion failures have been corrected; all three website
  roles remain subject to the simulated resource denial.
- Started a fresh headless Chrome profile, without accessing the user's login
  browser. Ran the existing full `workflow-browser.mjs` via its new CDP endpoint:
  all 20 reported browser scenarios passed, including desktop/mobile layout,
  concrete calendar/Bitable approvals, OAuth continuation, XSS filtering, member
  controls and unknown-scheduler recovery. Feishu/OAuth endpoints were mocked.
- New PPT authenticated-Blob browser cases are being added separately; the above
  suite does not yet prove that feature's browser behavior.
- Started current backend on an ephemeral localhost port with a fresh temporary
  database. Application startup succeeded. `/health` returned 200; unauthenticated
  `/auth/me` returned 401 with `no-store`; unknown private PPT returned 404.
  Baseline security headers appeared on all three responses.
- Isolated `/health` probe: 100 GETs, concurrency 10, zero errors, 73ms total,
  p50 2ms / p95 11ms in this local sample. This measures only the health route,
  NOT model inference, database-heavy chat throughput or real Feishu latency.
- Existing port 8000 service was not modified by this smoke test.
- Temporary backend received SIGINT and completed graceful shutdown (exit 0).

## Remaining real integration

### Recovery tooling evidence

Added `backend/data/sqlite_snapshot.py`: one-database consistent SQLite backup to
an exclusively created private destination, explicit connection closure, integrity
check and digest. Three isolated tests verify committed WAL inclusion, restore to
a new database, refusal to overwrite and cleanup of a failed new snapshot. No live
database was copied or restored in this step. Multi-database coordinated backup,
encryption-key retention, uploaded assets and off-host recovery still require the
deployment procedure documented in `enterprise-deployment.md`.

Latest consolidated backend run: **312 passed, 1 upstream warning in 15.11s**.

### Latest rollout and recovered-workflow learning

- A completed graph workflow may now form candidate memory after a recovered
  read-only failure. Its blueprint keeps only successfully executed operations;
  failed/unknown writes and untyped failures still prevent learning. The streaming
  completion path uses the same rule. Added read/write/untyped-failure tests.
- Pre-rollout consolidated suite: **315 passed, 1 upstream warning in 15.09s**.
- Stopped only the `feishu-cli-web` tmux service; its old pane exited and port 8000
  stopped listening. No unrelated service was changed.
- Secured pre-restart backup at `/private/tmp/feishu-pre-restart.XLlwBl`: copied
  the data directory, archived current code/assets/config (excluding dependencies
  and .git), and validated separate application and graph SQLite snapshots.
  Both quick_check results were ok; the gzip archive integrity check passed.
  The archive is the current tested worktree, not a reconstruction of the old
  in-memory server revision. Do not call it a guaranteed old-version rollback.
- Started the updated backend in tmux `feishu-cli-web`, pane `%44`, on the original
  `127.0.0.1:8000`. `/health` returned 200 with the new security headers;
  unauthenticated `/auth/me` returned 401 and no-store. The root page serves the
  rebuilt frontend asset `index-e52947cc.js`. Existing data was not reset.
- The earlier statements above that port 8000 had not been updated are historical;
  this entry supersedes them. Real multi-account and calendar/Bitable write
  acceptance is still missing and the overall goal remains incomplete.
- Parent reran the final browser suite including the new PPT cases: all 21
  reported scenarios passed. Private resource JSON/images/downloads carry auth;
  cross-origin URLs are rejected without sending credentials, and session switch
  revokes prior Blob URLs. The temporary test Chrome was then closed; no user
  browser profile was used or modified.

The user currently has no separate employee/lead/admin test identities. Isolation and
role changes can be tested with synthetic accounts; real multi-account Feishu ACL
verification remains unavailable. Calendar, meeting, document and Bitable write/read
lifecycles still require explicit per-feature evidence, not permission-list claims.

### Authorized live calendar / meeting / Bitable verification — 2026-09-08

Following explicit user permission, created isolated resources labelled
`网站测试-20260908-*`, using the website's bound `admin` account through
`LarkCLISkill.execute_command`, user identity, structured results and explicit
write approval. No bot fallback, colleague invitations or existing business-data
modifications. Installed CLI stayed at 1.0.93.

- Bitable create succeeded: [网站测试-20260908-B1](https://ucnn92h1qs1c.feishu.cn/base/U0qgb98dJa8azms9qIqckRE5nId).
  Table list returned `tblm6GAY3TO5eTeu`; field list verified the actual schema
  before writing. Added a single-select `验收状态` field (`fldlYEcIga`) with
  `待完成` / `已完成` options using the installed CLI's documented schema.
- Created exactly one record, `recvuDh8s8TTtu`, text
  `网站测试-20260908-B1-完成状态验收`, initially `验收状态: ["待完成"]`.
  Updated that returned ID to `["已完成"]`. Subsequent record-list readback
  returned that same ID and completed value, one row, `has_more: false`, rev 3.
  This is a persisted status update, not a UI checkbox-click test.
- Calendar create succeeded: `b7ec00fb-1aff-41ed-9c5c-3a01ed2cd760_0`.
  Readback included a real video meeting URL `https://vc.feishu.cn/j/459829185`.
  No meeting was joined or started. Attendee-list returned `[]` with
  `has_more: false`; no other participants were added.
- Updated only this test event, with `--notify=false`, to
  `网站测试-20260908-会议B1-已验收`, September 8, 2026, 12:00–12:10 Asia/Shanghai
  (past at verification time), avoiding a future test busy slot/reminder.
  Readback confirmed the exact title, dates and unchanged video URL.
  Visibility remains `default`, not explicitly private; do not claim otherwise.
- Both resources remain available for inspection; no cleanup/deletion performed.
  All writes and readbacks returned success with `identity: user`.

This closes current-account live calendar creation/update, video-link generation,
and Bitable create/record-update/readback evidence gaps. It does NOT establish
end-to-end natural-language browser execution, video attendance/recording,
document writes, delete permissions, real cross-role ACL behavior or high-load
performance. Earlier missing-evidence statements are superseded only for the
specific operations above; the overall improvement goal remains incomplete.
