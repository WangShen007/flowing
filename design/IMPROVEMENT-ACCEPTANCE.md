# Enterprise agent improvement acceptance ledger

Objective: reliable natural-language Feishu work across messages, documents, tasks,
calendar/meetings and Bitable; differentiated employee/lead/admin management; safe,
comfortable desktop/mobile UI; automated and real integration verification.

Status: IN PROGRESS. Unit tests do not establish real Feishu functionality.

## Workstreams and evidence required

| Requirement | Acceptance evidence | Status |
| --- | --- | --- |
| Safe chat rendering and login | HTML attack regression, legacy password migration, bootstrap and throttling tests | Implemented and covered by current backend suite; final browser/deployment check pending |
| Scheduler recovery and duplicate prevention | Concurrent claim and crash-boundary tests; uncertain writes not replayed | Local multiprocess/exception tests pass; live scheduler restart verification pending |
| Role and resource boundaries | Employee/lead/admin, disabled and unknown users; ownership and scope negative tests | Existing coverage plus audit underway |
| Calendar, meeting, Bitable, docs, messaging and tasks | Catalog/schema/scope contracts, positive/negative workflow tests, isolated live resources | Inventory underway; live verification pending |
| Flexible language and multi-turn entity resolution | Real model evaluation with typos, ambiguity, referents and partial completion | Three real-model/synthetic-Feishu cases pass; broader task variety and structured entities remain |
| Relevant, isolated, revocable memory | Retrieval, stale-data, per-user isolation, verified-success checks | Isolation/revocation and context budget covered; semantic retrieval quality unverified |
| Goal completion evidence | Multi-operation partial failure and resume without replay | Separate-write approvals and uncertain-write false-completion rejection pass; arbitrary multi-goal completeness remains unproven |
| Responsive UI and OAuth recovery | Build plus desktop/mobile browser tests, popup-blocked flow, readable approvals | Implementing |
| Observable latency and meaningful recovery | Per-stage timings, error classification, read retry and unknown write reconciliation | Active processing timings implemented; recovery work underway |
| Production deployment and maintenance | Config hardening, repeatable CI, backup/restore and retention validation | Pending |

## External test boundaries

Use user identity and explicitly named disposable test resources. Do not send to real
business groups, invite coworkers, modify others' resources, or expand administrator
scope just to make a test pass. Record application permission, user grant, website role
and resource ACL separately. Missing grants or test identities are verification gaps,
not evidence that a feature works. Preserve all existing data and uncommitted work.

Update: user explicitly allowed tests in all existing groups; use few clearly marked
messages and no unnecessary mentions. There are currently no separate real role-test
accounts. A single-group real messaging/idempotency test is recorded in
`IMPROVEMENT-TEST-RESULTS.md`; it does not close other functional verification gaps.

## Baseline

Prior session reported 216 backend tests passing. This is historical evidence and must
be rerun after the current changes. Current repository: /private/tmp/feishu-cli-web.LN5DMU.
Do not mark the overall goal complete until every requirement above has current evidence.
