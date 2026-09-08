# Beijing time / confirmation repairs — D1

## Implemented

- Default agent clock and naive calendar/scheduled dates use Asia/Shanghai,
  independent of server/browser timezone. Explicit offsets remain supported.
- Model-facing native calendar creation takes datetime/date, not Unix timestamp.
  Backend converts datetime to epoch; validates offsets, ordering and DST
  ambiguity/nonexistent local times. Approval retains human-readable input;
  actual CLI receives converted timestamps. All-day dates remain dates.
- Clarification completion must provide a concrete question; server displays that
  question, not arbitrary claims that a plan is awaiting confirmation. Regression
  verifies the former empty-command “waiting confirmation” response is rejected,
  and the model can continue into a real interrupt/approval.
- Bitable preview handles actual string JSON batch payloads, including record IDs
  and field changes. Unparseable payload remains visible as raw JSON.
- Repeated describe requests reuse current-run definitions. Model-facing native
  descriptions omit duplicate output schema/help and shorten prose metadata;
  full input validation remains on the server. No permission relaxation or
  removal of model reasoning.

## Verification

- Backend: 346 tests passed, one upstream Starlette warning, 17.32s.
- Frontend vue-tsc/Vite build passed; git diff --check passed.
- Browser suite: 21 scenarios passed in isolated headless Chrome, including
  actual JSON-string Bitable payload preview. Added waits for asynchronous
  session rendering rather than asserting before content appears. Earlier runs
  on shared Chrome encountered rendering/CDP timeouts and are not counted as
  full passes. Isolated test browser was closed afterward.
- Updated local website service was restarted on port 8000; health returned ok.

## Real browser / real model / real Feishu

Session `0658631b-486c-4018-8a84-57cdd1d19a2a`:

1. Requested video meeting “网站北京时间回归-D1”, September 9 12:20–12:30,
   without explicitly stating a timezone. Initial model request timed out
   (OpenAI APITimeoutError from upstream ReadTimeout), before any write.
2. Continuing produced correct Asia/Shanghai wall-clock approval. That initial
   plan was not approved. It omitted explicit reminders=[], so this is not
   considered a clean first-attempt acceptance of every requirement.
3. Requested a correction to 12:40–12:50 and explicitly empty reminders. Real
   replacement confirmation card appeared; no false clarification ending.
4. Clicked the corrected card. Exactly one create succeeded, then event get
   returned start=1788928800/end=1788929400, Asia/Shanghai, confirmed status,
   title 网站北京时间回归-D1, and meeting URL https://vc.feishu.cn/j/550424435.
   Event ID: `3aec7450-9707-4b60-a461-9875b9e1cf10_0`.
5. No group-message or attendee-add call executed. No meeting joined. Event
   retained at September 9 12:40–12:50, busy, default calendar visibility.
   Meeting defaults anyone_can_join, auto_record=false; response explicitly
   explained that no invitations does not imply organizer-only admission.
6. Corrected run active processing 40.587s, excluding approval waiting, initial
   timeout and prior request. This is NOT a demonstrated latency improvement;
   upstream model timeout/latency remains a limitation. Native schema context
   reduction is implemented but requires repeated comparable benchmarks.

Reminder creation input was [], response omitted the reminder field; no claim
of explicit empty-array readback. Real role-revocation tests still unavailable.
Historical pending plans should be regenerated, not blindly replayed.
