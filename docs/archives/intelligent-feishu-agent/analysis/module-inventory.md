# Module Inventory

| Module | Responsibility | Complexity | S.U.P.E.R Score |
|:--|:--|:--|:--|
| `api/routes/chat.py` | Preview, execution, streaming and persistence orchestration | High | S🟡 U🟡 P🟡 E🟢 R🟡 |
| `core/scheduled_tasks.py` | Schedule parsing, persistence and runner | High | S🟡 U🟢 P🟡 E🟢 R🟡 |
| `skills/lark_cli/skill_runtime.py` | Skill discovery, planning, execution and repair | Critical | S🔴 U🟡 P🟡 E🟢 R🔴 |
| `core/execution_records.py` | Immutable account/session execution history | Low | S🟢 U🟢 P🟢 E🟢 R🟢 |
| `core/workflow_memory.py` | Planned account-scoped reusable experience boundary | Medium | S🟢 U🟢 P🟢 E🟢 R🟢 |
| `core/storage.py` | SQLite schema and primitives | Medium | S🟢 U🟢 P🟡 E🟢 R🟡 |
| `frontend/src/components/Chat.vue` | Main chat workspace and execution UX | Critical | S🔴 U🟡 P🟡 E🟢 R🔴 |
| Backend tests | Observable API, storage and workflow behavior | Medium | S🟢 U🟢 P🟢 E🟢 R🟢 |
| Browser tests | User-visible confirmation and failure behavior | Medium | S🟢 U🟢 P🟢 E🟡 R🟡 |

## Module Details

### Intent and schedule routing

- Responsibility: decide whether a request is an immediate Feishu operation, a scheduled operation, or ambiguous.
- Current issue: a generic date/time can pre-empt semantic planning even when it is message content.
- Transformation: introduce pure, typed classification helpers and keep route orchestration one-directional.

### Lark CLI runtime

- Responsibility: select skills/references, create safe plans, and execute commands.
- Current issue: the 5,000-line module combines too many concerns; model fallback lacks a confidence/clarification contract.
- Transformation: add a replaceable intent-understanding port while preserving existing command builders.

### Workflow memory

- Responsibility: capture only verified successful plan blueprints, retrieve only the current account's relevant memories, and expose activation/deletion controls.
- Boundary: memory is a cache and planning hint, never authorization or permission evidence.

### Frontend

- Responsibility: make provenance, ambiguity, confirmation, learning status, and deletion visible.
- Transformation: extend existing cards and account settings rather than introducing another visual system.
