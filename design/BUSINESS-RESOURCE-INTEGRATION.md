# Business resource integration — first increment

Implemented independently from the ideas reviewed in liangdabiao/lark-crm-feishu-cli
and Byte-n/larkDocx2md; no external source or dependency was copied/installed.

## Delivered

- `business_resources` graph tool: list, save/replace, delete. SQLite-backed,
  exact website account isolation, at most 30 aliases/account and 30 field aliases
  per resource. Foreign key deletes configurations when their account is deleted.
- Save/delete use existing durable confirmation. After confirmation, save reads
  the target fields with the account's existing CLI identity and verifies mapped
  field IDs. Scheduled tasks cannot modify configurations. Deletion only removes
  the local alias; it never deletes Feishu records or tables.
- Records use fresh field-list validation at the shared execution boundary for
  upsert, batch create and batch update, including after human approval. Rejected
  input never starts the write process. Missing/ambiguous fields, unsupported or
  computed types, invalid select values and wrong CellValue shapes fail closed.
  Platform permissions remain authoritative; configuration grants no access.
- Chat confirmation displays alias, resource and field mapping with explicit
  local-only scope. Configurations are retrieved on demand, not injected into
  every model request. They are distinct from workflow-method memory.
- Agent instructions use existing docs +fetch outline/section/block anchors,
  require source/read-scope disclosure and record readback, and prohibit claiming
  full coverage for partial results. No duplicate Markdown converter added.

## Examples

- “把这个多维表格记作项目表，完成进度对应状态字段。”
- “查看我保存的业务资源配置。”
- “忘记项目表的配置，不要删除飞书里的数据。”

The model must first resolve actual table/field IDs; the server validates save
after confirmation. Renamed fields can still be addressed by saved stable IDs;
deleted/replaced IDs must be remapped. Record names, option meanings and linked
record identities are not permanently cached or inferred by the server.

## Boundaries / remaining work

- This is not an automatic CRM installation or a separate settings dashboard.
- Field guard validates supported shapes, not arbitrary business constraints or
  existence/ownership of every linked ID; the agent must resolve IDs and the
  platform enforces resource ACLs. No deletion capability was added.
- Fresh schema reads add one read per write; no measured latency improvement is
  claimed. No global cross-account cache and no increase in Feishu scopes.
- Document completeness is currently an execution instruction, not a structured
  converter coverage report. Whiteboard parsing, media extraction and quantitative
  completeness reporting remain a later increment requiring capability fixtures.
- Tests use isolated databases and mocked model/Feishu responses. Real-model
  natural-language E2E and browser confirmation interaction are not yet verified
  for these new operations. Runtime rollout is tracked separately.

## Verification and rollout

- Backend suite: 334 passed, one upstream Starlette deprecation warning, 15.60s.
- Frontend: vue-tsc and Vite production build passed.
- git diff --check passed before rollout.
- Gracefully stopped only the existing feishu-cli-web process (PID 59103), then
  restarted that tmux service on the same localhost port 8000. Existing databases,
  credentials and unrelated services were not reset or replaced.
