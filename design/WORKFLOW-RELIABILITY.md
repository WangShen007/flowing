# Conversation Workflow Reliability

Updated 2026-09-08. This change addresses cancellation, conversation switching,
authorization continuation, and retrieval of local CLI skill references.

## Research Applied

- [Vercel AI SDK chat state and stopping](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot):
  distinguish submitting, streaming, ready, and error; cancel the active request
  rather than disabling the entire conversation interface.
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts):
  associate an authorization interruption with a persistent conversation and
  checkpoint; resume the saved work instead of treating authorization as the end
  of the user's task.
- [LangGraph durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution):
  completed side effects must not be replayed on continuation. This implementation
  stores successful command results and retries the failed authorization step.
- [LangChain retrieval](https://docs.langchain.com/oss/python/langchain/retrieval):
  improve query preprocessing and evaluate retrieval against relevant references
  before introducing a vector store. Chinese bigrams and action aliases now match
  Chinese requests to the existing Chinese/English CLI documentation.
- [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/):
  V4 enables high-effort reasoning by default and exposes a request-level thinking
  toggle. Preview requests now use a compact non-trace semantic pass; confirmed
  execution replans with the provider default reasoning mode.

The project retains Vue, FastAPI, SQLite, and its existing CLI execution engine.
These changes apply the documented patterns; they do not add LangGraph or the AI
SDK as runtime dependencies and do not claim a complete framework migration.

## Implemented Behavior

- The composer remains editable during planning and execution. A stop icon cancels
  planning or execution; submitting another message first stops the active run.
- Switching history or opening a new conversation keeps executing chat streams
  running in the background while this page remains open. Returning to the chat
  reattaches its live messages and stop control. Only explicit Stop cancels the run.
  Late callbacks cannot change the new conversation, its messages, URL,
  plan, or authorization card. Unsent drafts are retained per conversation in the
  current page. Authorization requests are also detached on a view change.
- Read-only plans proceed without an extra confirmation click. Write plans keep
  explicit confirmation; that confirmation is passed to the executor.
- The server cancels the active async workflow and terminates the local CLI
  process group on POSIX. User requests are persisted when streaming starts;
  stopped and failed responses are retained in history. Completed steps are
  emitted incrementally, so stopping does not discard their execution record.
- Concurrent streaming requests for the same conversation are rejected.
- Authorization failures produce SQLite checkpoints bound to account and
  conversation. Resume atomically consumes the checkpoint, restoring the original
  request, confirmation state, and completed commands. It cannot accept a client
  replacement query or reuse another account's checkpoint.
- After authorization succeeds, the UI resumes the checkpoint automatically.
  Reloading a waiting conversation restores the continuation control. A user can
  also click Continue Task or enter the Chinese equivalent of "continue".
- Ambiguous "everyone attends" requests and placeholder participants are clarified
  before CLI authorization or writes. Follow-up input retains the original request.
- A meeting's date/time is distinguished from a background execution schedule.
- Common action typos are normalized only in the action phrase before the payload
  delimiter. Recipient names, group names, message bodies and times are preserved.
- Successful workflows produce account-scoped candidate memories. A user can
  activate one immediately, or a second independent success activates it. Exact
  active requests can reuse a plan; similar requests only provide a parameter-free
  blueprint to the model. Dynamic IDs, permissions and confirmation are never reused.
- Concurrent success/failure updates are serialized by SQLite transactions.
  Clarification, cancellation and confirmation waits do not count as failures.

## Verification

- Backend suite: 158 tests passed, including intent, memory, concurrency and account-isolation regressions.
- Browser harness: 14 groups passed against built frontend assets with an isolated
  mock API in a real Chrome tab. Covers stopping a delayed plan, switching during
  streaming, preserving drafts, submitting while running, ignoring delayed session
  events, restoring an authorization checkpoint after reload, automatic resume,
  clarification, and desktop/mobile overflow checks.
- Browser screenshots: /tmp/feishu-workflow-desktop.png and
  /tmp/feishu-workflow-mobile.png. Both visually inspected.
- Real application HTTP test: cancellation acknowledged in approximately 0.01s;
  cancelled conversation persisted; a subsequent request in that conversation
  successfully fetched an existing Feishu test document.
- The actual meeting preview endpoint returned a request for attendees before
  authorization. No meeting invitations or messages to other people were sent.
- Authorization UI callbacks were simulated. Checkpoint persistence, account
  isolation, single-use semantics, and avoiding replay of successful writes were
  independently exercised against the actual backend code with isolated SQLite.
  A fresh real Feishu permission grant was not part of this round.
- Frontend production build and focused lint/diff checks passed.

Browser harness command (Node with built-in WebSocket; no extra npm dependencies):

```sh
cd frontend
node tests/workflow-browser.mjs '<Chrome browser CDP websocket URL>'
```

## Plan Preview Reliability Follow-up

The September 8 group-notification incident exposed two planning defects: common
Chinese notification wording missed the deterministic route, and the 20-second
API deadline cancelled a model call configured to wait for 25 seconds. The UI
also hid the pending request until execution, so a second failed request appeared
to belong to the preceding answer.

The repair applies these upstream patterns without adding a framework dependency:

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
  treats orchestration, persistence, human review, and tracing as explicit runtime
  concerns. Its functional API also calls out deterministic replay and idempotent
  side effects. This project now routes well-defined group notifications locally
  and keeps execution behind the existing confirmation boundary.
- [OpenAI Agents SDK tools](https://openai.github.io/openai-agents-python/ref/tool/)
  expose strict tool schemas, approval gates, guardrails, and per-tool timeouts;
  [model settings](https://openai.github.io/openai-agents-python/models/) separately
  bound each model attempt. Here, preview model work has its own 8-second budget,
  transport retries are disabled for that bounded attempt, the API has at least a
  2-second cleanup margin, and the browser retains a 30-second network safety cap.
- [AutoGen tools](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/tools.html)
  pass cancellation tokens into tool execution. The existing explicit Stop flow
  remains the only path that cancels execution; preview cancellation never starts
  a Feishu tool command.

Implemented behavior:

- Group sends accept `【】`, `「」`, `『』`, curly/straight quotes, explicit group
  prefixes, and unquoted names ending in `群`/`群聊`/`群组`. `发消息`, `发送消息`,
  `发通知`, and `发送通知` share one parser. Group syntax is checked before the
  permissive direct-message parser, while negated requests are rejected.
- Every preview reports `planning_source` and `planning_duration_ms`. Known
  requests show local deterministic routing; model failures return a conservative
  fallback before the API deadline instead of being killed by an inverted timeout.
- The preview card always shows the pending request. Failure states explicitly say
  that no command or Feishu message was sent and provide Retry, Edit, and Cancel.

Verification for this follow-up:

- Backend suite: 158 tests pass, including notification variants, person/group
  disambiguation, negation, deterministic no-model routing, timeout hierarchy,
  preview transport retry policy, and the complete search-chat-to-send-command
  planning chain.
- Frontend production build passes. The isolated Chrome harness passes all 14
  behavior groups, including the new timeout/no-side-effect/edit recovery checks.
- On the real local service, `给「资料管理群」发送通知：诊断测试，不执行` returned
  HTTP 200 through the deterministic route in 107 ms server time. A separate UI
  run rendered the complete confirmation card in 14 ms server time. It was
  cancelled at preview; the conversation still contained exactly its original two
  messages and no Feishu send command ran.
- The reported typo notification previews in 22–151 ms after warm-up and remains a
  group message even when its body contains “明天下午三点”. A cold composite request
  now reaches the model in about 2.0–3.4 seconds instead of timing out near 9 seconds;
  it returns a concrete clarification and no commands when confidence is low.
- Existing scheduled task 6 reached its original 2026-09-08 15:00 Asia/Shanghai due
  time during final restart. Its placeholder-participant guard returned
  `requires_input=true` before any CLI command; it is now `failed` with one run and
  did not create a calendar event, invite anyone, or send a message.

## Boundaries

September 8 follow-up: the group-list workflow now uses the installed CLI's
verified `im +chat-list --as user` shortcut, including bounded auto-pagination.
The actual read-only command reached authorization validation and reported missing
`im:chat:read`; no group list was retrieved. A dedicated regression checks both
planning and execution-step selection without model-generated resource names.
The updated browser test checks that switching sends no cancellation request,
another conversation can run independently, and returning shows the original
stream completing. The current backend suite has 158 passing tests.

- Stop cannot undo a request already accepted by Feishu. The interface records
  this uncertainty; a cancelled run is not automatically replayed.
- The active cancellation registry is process-local and matches the current
  single-worker development server. Multiple workers need shared run ownership
  and cancellation signaling before deployment.
- Only authorization interruptions have durable continuation checkpoints.
  Arbitrary execution interrupted by a service crash is not automatically resumed.
- Full task model calls dispatched through synchronous SDKs may still finish in
  their worker thread after cancellation, although subsequent tool execution is
  stopped. Preview model calls now use a bounded SDK transport with retries
  disabled, limiting this exposure to the wider execution workflow.
- Retrieval is still local lexical retrieval with query expansion, not an embedding
  RAG service. The meeting reference regression is useful evidence, not a broad
  retrieval benchmark.
- Existing scheduled task 6 contained placeholder participants. When it became due,
  the clarification guard stopped it before CLI execution; operators must create a
  new task with real participants rather than assuming the failed placeholder task ran.
