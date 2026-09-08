# Risk Assessment

## S.U.P.E.R Architecture Health Summary

| Principle | Status | Key Finding | Priority |
|:--|:--|:--|:--|
| Single Purpose | 🔴 | Planner and chat component are oversized | High |
| Unidirectional Flow | 🟡 | Route-level schedule detection pre-empts semantic routing | High |
| Ports over Implementation | 🟡 | Plans are serializable, but intent and memory contracts are implicit | High |
| Environment-Agnostic | 🟢 | Settings and SQLite paths are configurable | Low |
| Replaceable Parts | 🔴 | Planning behavior is tightly coupled to one runtime class | High |

**Overall Health**: 1/5 principles healthy — focused refactoring required.

## Risk Matrix

| Risk | Impact | Likelihood | Mitigation |
|:--|:--|:--|:--|
| Event time mistaken for execution time | Wrong scheduled side effect | High | Require execution cues/order; clarify ambiguity |
| Model typo correction alters recipient/content | Wrong message | Medium | Preserve entities/payload; confirmation remains mandatory |
| One lucky execution poisons memory | Repeated wrong plans | Medium | Candidate state, second-success/manual activation, failure demotion |
| Cross-account memory leak | Privacy and authorization breach | Low/critical | Account key on every query and API dependency |
| Stale IDs or scopes reused | Wrong target or failed writes | Medium | Store command shapes, re-resolve IDs, recheck permissions |
| External model latency/outage | Slow or empty preview | High | Fast deterministic path, bounded model call, actionable clarification |

## Technical Debt

The new work will not attempt a wholesale split of `skill_runtime.py` or `Chat.vue`; it introduces narrow typed modules so future extraction is possible without destabilizing the existing dirty worktree.

## Testing Risks

Live-model interpretation is nondeterministic and must not be the only CI evidence. Regressions will test public parser/API behavior with mocked model results, plus a live plan-only smoke test after restart.

## Project Governance Risks

There is no repository-native instruction or engineering memory surface. No new project-agent memory file will be created without explicit user selection. The product's user workflow memory is application data and is not a replacement for repository governance.

## Compatibility Concerns

Existing chat/plan fields remain valid. New memory metadata and endpoints are additive. Database initialization must migrate existing SQLite files without deleting records.
