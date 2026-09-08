# Model-driven Feishu execution

Implemented 2026-09-08. Default engine: `langgraph`; rollback setting: `LARK_AGENT_ENGINE=legacy`.

## Architecture

- `agent_graph.py`: LangGraph model/tool loop with native OpenAI-compatible and Anthropic tool calling. DeepSeek reasoning content is retained between tool calls. No command-string plan parsing in this path.
- `tool_catalog.py`: discovery reads the same `COMMANDS` registry as enterprise policy. Descriptions inspect installed CLI help/schema on demand. JSON Schema validates arguments; the server constructs argv and supplies identity.
- Ordinary preview returns immediately; no tool call is executed by preview. All normal natural-language requests use the graph, including structural scheduling. Existing explicit CLI and specialized PPT paths remain separate.
- Writes interrupt before execution. Existing account-scoped, single-use resume handles point to LangGraph checkpoints. Confirmation restores the exact pending call; it does not authorize subsequent arbitrary writes.
- SQLite checkpoints persist model messages, pending calls, schemas and observations. A write receipt recorded before execution prevents blind replay after an uncertain interruption. This is not an exactly-once delivery guarantee from Feishu.
- Server-created scheduler runs can execute their previously confirmed task; role and grant policy still applies at execution. Unsupported recurrence remains unsupported and must be clarified.
- Successful runs contribute candidate procedures. Active per-account procedures are context for model judgment, not executable plans or fresh facts.

## Sources

- https://docs.langchain.com/oss/python/langgraph/quickstart
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- https://github.com/HKUDS/nanobot
- https://github.com/bytedance/deer-flow

## Validation and boundaries

- Unit regressions cover model observations, durable write approval, authorization continuation, cross-account isolation, schedule creation, provider retry and schema validation. Legacy engine regressions explicitly select the legacy engine.
- Installed CLI inspection confirms `im +chat-members-list` exists. The old bundled reference set omitted it. `im chats get` exposes user_count and bot_count with existing group-read permissions; listing individual members may require additional scope.
- First live graph test of “齐步走这个群聊有几个人” completed two read operations and returned 1 user / 0 bots in 11.6 seconds.
- Follow-up exposed premature consent requests when the model considered a member-list operation even though group metadata already contained counts. Missing scope is now returned to the model first; a separate authorization tool interrupts only when the model cannot complete using existing evidence or allowed alternatives.
- Final live API conversation: “齐步走有几个人” (12.97s), “那机器人有几个，别把人和机器人混在一起” (9.12s), “齐步走这个群聊有几位成员呀” (11.12s) all completed with two read operations and verified 1 user / 0 bots. Genuine successful API records created candidate/active memories.
- Conversational “给刚才那个群捎句话…” resolved the previous group and stopped at an exact message approval checkpoint; no send was executed.
- Validation: 167 backend tests, 15 isolated Chrome interaction groups, Vue TypeScript/Vite production build, focused Ruff, compileall and diff checks passed. Browser coverage includes reload during tool approval and single checkpoint resume.
- No real message delivery is part of acceptance; live send checks stop at the persisted approval boundary.
- Model quality still matters. The registry intentionally contains audited business operations only; discovery does not grant unsupported operations. Multi-worker ownership and remote durable stores are future deployment work.
- SQLite checkpoints contain business data and require the same server access protection and retention policy as conversation history. Procedure memory is model-generated, user-scoped and editable by disabling/replacing; it is not a guarantee of future success.
