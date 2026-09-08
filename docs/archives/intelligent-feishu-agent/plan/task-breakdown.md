# Task Breakdown

## Overview

- **Total Phases**: 3
- **Total Tasks**: 6
- **Mode**: LOCAL_ONLY (GitHub repository lookup failed; no external resources created)
- **Delivery**: one local coherent batch on the existing dirty worktree, with no commit or push

## S.U.P.E.R Design Constraints

- Pure intent classification must be independently testable without Feishu or a model.
- Model understanding and memory retrieval use typed, serializable contracts.
- Memory is account-scoped, parameterized, and never bypasses current permission or confirmation checks.
- Existing API behavior is preserved; additions are backward compatible.

## Phase 1: Correct understanding

| Task | Priority | Effort | Depends | S.U.P.E.R | Tests | Acceptance |
|:--|:--|:--|:--|:--|:--|:--|
| T1.1 Distinguish content time from execution time | P0 | M | — | S,U,P | Schedule/API regression | Message content is never scheduled solely because it contains a date/time |
| T1.2 Add bounded structured AI understanding and typo-safe normalization | P0 | L | T1.1 | P,R | Planner tests | Common action typos normalize without changing targets/content; ambiguity asks a question |

## Phase 2: Verified user workflow memory

| Task | Priority | Effort | Depends | S.U.P.E.R | Tests | Acceptance |
|:--|:--|:--|:--|:--|:--|:--|
| T2.1 Add account-scoped memory schema/store | P0 | M | T1.2 | S,U,P | Store/API isolation tests | First success creates candidate; second success activates; other accounts cannot read it |
| T2.2 Retrieve safe plan blueprints and record outcomes | P0 | L | T2.1 | U,P,R | Workflow tests | Exact active memories are fast; similar memories assist planning; writes still confirm |
| T2.3 Add activate/delete APIs | P1 | S | T2.1 | P,R | API authorization tests | Current user can manage only their own memories |

## Phase 3: User experience and validation

| Task | Priority | Effort | Depends | S.U.P.E.R | Tests | Acceptance |
|:--|:--|:--|:--|:--|:--|:--|
| T3.1 Show clarification, memory provenance and controls; run full regression | P0 | M | T2.2,T2.3 | S,P | Build/browser/backend | UI exposes candidate/active status and never claims execution during preview |

## Parallel Lanes

Execution is sequential because the affected route, storage, planner and UI already have overlapping uncommitted edits. This minimizes merge risk.

## Delivery Batch

`LOCAL-B1` contains T1.1–T3.1. Combined validation: targeted pytest, full backend pytest, frontend production build, browser regression, and live plan-only smoke tests.
