# Enterprise identity and authorization

## Intended behavior

Employees use one company-owned Feishu application. A verified tenant and user
identity maps to one website account, shared across devices. Each device has its
own website session; personal Feishu grants belong to the server-side account.
Refresh valid credentials without interaction. Ask for missing grants only when
necessary, and resume the failed workflow step without replaying successful writes.

## Delivery and evidence

- Implemented, unit-tested: backend membership roles (employee, lead, admin), administrator-only
  global settings, audited membership changes, last-administrator protection,
  disabled-account checks at authentication, scheduler and CLI execution boundaries.
  Validation: `backend/tests/test_account_access.py`.
- Implemented for catalogued commands: user-scope capability catalog and role
  checks before CLI execution and after token refresh. Unknown enterprise commands
  are denied. All 50 built-in templates now have an explicit enterprise policy:
  18 reviewed templates are enabled for admin/lead and 32 are fail-closed pending
  command/scope review. This is policy coverage, not end-to-end proof of all 18.
- Implemented, live-tested: company app environment configuration, OAuth and
  PKCE with browser-bound, single-use state; verified tenant/open_id binding and
  cross-device account lookup. Authorization-code exchange uses v2 for the
  documented authorize-endpoint PKCE compatibility; refresh uses v3.
- Implemented, live-tested: AES-GCM user token storage bound to the website account,
  per-account process lock for rotating refresh tokens, expiration/revocation
  handling, independent 30-day website sessions. CLI user token environment
  integration successfully queried groups using the server-held personal grant.
- Implemented and mock-tested: role permission bundles, request-missing-scopes-only,
  reuse of valid grants, enterprise authorization popup and checkpoint continuation.
  Enterprise mode rejects the legacy per-user app-creation endpoint.
- Implemented and browser-tested: member-management dialog, role/enabled controls,
  connection status and disconnect. Disconnect preserves identity/history while
  revoking local use before remote revocation; failed remote revocation is retryable.
  In-flight bound callbacks are invalidated using account authorization epochs.
- Existing implementation to revalidate: account-bound workflow checkpoints and
  single-use resume, successful-write preservation, conversations continuing while
  switching between views in an open browser.
- Pending: physical mobile access through a shared HTTPS origin and real
  different-person isolation/downgrade tests. Refresh, scheduled-job checks and
  deployment/callback documentation have been validated locally.

## Boundaries

Website roles do not grant access beyond the employee's Feishu resource permissions.
New-device identity verification remains necessary. Revoked or expired refresh
credentials can require consent again; permanent authorization is not promised.
Current CLI profiles and existing scheduled tasks must be preserved during migration.
Passing unit tests alone does not prove live Feishu authorization or refresh works.

## Verified references (2026-09-08)

- https://open.feishu.cn/document/authentication-management/access-token/obtain-oauth-code.md
- https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/get-user-access-token-v3.md
- https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/refresh-user-access-token-v3.md
- CLI source commit `c2afcce1b52c95d0d5e9508fdf9ac5585f545ceb`,
  `extension/credential/env/env.go` and `internal/envvars/envvars.go`:
  `LARKSUITE_CLI_APP_ID`, `LARKSUITE_CLI_USER_ACCESS_TOKEN`, strict user identity.
  Installed CLI 1.0.93 recognizes external credentials and rejects interactive
  `auth status` for them. Therefore enterprise probes use the server grant store.

The official refresh documentation requires fresh user authorization after 365
days. Token lifetimes come from responses, not hard-coded OAuth lifetimes.

## Current activation boundary

Enterprise configuration is enabled for the local test service. Existing admin
identity migration and live OAuth/refresh are verified. The scenario catalog is
explicitly partitioned into reviewed and disabled templates; advanced disabled
templates still require command/scope review before broader deployment. Current
role-bundle scopes are enabled and have passed real administrator consent.
The existing user's app profiles and scheduled tasks are preserved.

Validation completed in the latest implementation pass: 127 backend tests pass;
frontend TypeScript and Vite build pass; browser harness passes all 13 output
groups. Real Chrome also verified enterprise scenario badges and disabled-form
behavior. These checks complement, but do not replace, the live Feishu checks below.

Real Chrome connection recovered via the authorized remote-debugging prompt.
The test app is enabled and published in the verified tenant. The local callback
`http://127.0.0.1:8000/login` appears in its redirect settings. Additive user
permissions described below were published; no existing permissions were removed.

## Latest live verification

On 2026-09-08, verified `offline_access` is enabled for user identity. Found
`im:chat:read` was enabled only for application identity. Enabled its user-identity
variant in the real test app and verified the permission list reports enabled.
Completed the single-scope device consent for the existing admin CLI profile;
the CLI verified the same open_id `ou_1efc712d51c795e4eade752129aea0a2`.
The real `im +chat-list --as user --page-all --page-limit 10 --page-size 100
--format json` command subsequently returned exit 0, `ok: true`, and group data.
This verifies the existing CLI profile, not the new enterprise OAuth bridge.

Separated operation scope requirements from onboarding bundles, preventing read,
create, update, attendee and message-search commands from requiring unrelated
permissions in the same bundle. Backend regression suite: 103 passed.

## Cross-device authorization verification (2026-09-08)

- Real Chrome authorization initially failed with code 20049. The authorize
  documentation explicitly directs PKCE clients to the v2 exchange endpoint.
  Using that endpoint with JSON succeeded; PKCE remains enabled, and refresh
  continues through v3. No automatic downgrade retry is performed.
- A fresh OAuth flow without a website bearer returned to the existing admin
  account: session count increased by one and identity count remained one.
  The consent screen displayed historical permissions, with no additional
  business permissions requested. Feishu may still require identity confirmation.
- Unauthenticated login requests only offline_access. Authenticated onboarding
  still supports role bundles and requests missing scopes only.
- Real v3 refresh succeeded, rotated the refresh token, and retained scopes.
- Two independent HTTP clients received different website sessions for the same
  account; both reused the grant. Logging out one left the other valid.
- Fixed enterprise command checks to use logical CLI arguments before launcher
  resolution. Absolute executable paths and Node launchers no longer cause false
  policy denials. Real enterprise group listing then succeeded.
- Focused identity/account suite: 27 passed. No physical phone test or full
  scenario/scheduler revalidation is claimed by this pass.

## Role bundle and scheduler verification (2026-09-08)

- Enabled 19 additional user-identity permissions via the test application's
  additive batch importer. The console reported all changes published. Existing
  tenant permissions were not removed. The current 25-scope administrator bundle
  is fully granted; employee/lead restrictions remain enforced by the website.
- Added verified calendar +freebusy and +room-find command mappings using
  calendar:calendar.free_busy:read. Live freebusy lookup succeeded.
- Real incremental consent exposed a missing refresh credential: subtracting
  previously granted offline_access omitted it from the new request. New consent
  requests now always retain offline_access. Reauthorization followed by v3
  refresh succeeded with every current role scope retained.
- Created scheduler test task 7 through /api/v1/chat, in session
  enterprise-scheduler-live-20260908, due 2026-09-08 02:12 Asia/Shanghai.
  The service's normal scheduler completed it once, successfully querying groups
  and persisting its result. Task 6 was preserved at this checkpoint; it later
  reached its due time and was blocked before CLI execution because its attendee
  fields were placeholders, as recorded in `WORKFLOW-RELIABILITY.md`.
- Employees no longer see model settings or global scheduler controls; their
  personal task list and controls remain available. Twelve browser harness
  groups passed, including employee/admin differences, plus frontend build and
  desktop/mobile screenshot checks. Test Chrome was closed afterward.
- Unit tests now use a temporary FEISHU_CLI_DATA_DIR and isolated Settings inputs,
  so local company configuration cannot enable enterprise behavior accidentally
  or redirect test writes into the live account database. Full suite: 108 passed.
- Remaining: scenario catalog coverage beyond the currently reviewed commands,
  live workflow continuation after missing permission, multi-user enterprise
  validation, and physical mobile access through a shared HTTPS origin.

## Authorization continuation verification (2026-09-08)

- Fixed uninitialized plan/result references in direct-command policy denials.
  Planned and repair-generated commands now also stop immediately on policy
  denial rather than feeding role restrictions into the model repair loop.
  Regression tests cover all three paths.
- Added calendar +suggestion, minutes +search and minutes +detail mappings from
  official CLI shortcut definitions. Minutes metadata requires basic:read;
  requesting summary/todo/chapter/transcript/keywords also requires artifacts:read.
- Enabled the three corresponding minutes user scopes in the test application.
  The role catalog now contains 28 unique scopes including offline_access.
- Real /api/v1/chat request in enterprise-resume-live-20260908 stopped for the
  ungranted minutes search scope and created a waiting checkpoint. Reloaded that
  conversation in real Chrome, used its authorization button, completed real
  Feishu consent in a popup, and observed automatic continuation in the same
  conversation. The popup closed, checkpoint became consumed, and the query
  returned ok:true with an empty result set. This verifies one real read-only
  continuation; successful-write preservation remains mock-tested.
- Enterprise authorization cards now present one authorize-and-continue action;
  legacy CLI setup steps are hidden in enterprise mode. Scope identifiers remain
  in metadata while the user receives a short explanation.
- Direct command results use the existing readable summary path, retaining raw
  stdout/stderr and executed command evidence. A real DeepSeek-backed repeat
  returned "未找到‘企业授权测试’相关的妙记" instead of dumping JSON.
- Validation: 117 backend tests passed; frontend build and all 12 browser harness
  groups passed, including enterprise authorization action visibility. Independent
  test Chrome was closed. The local server remains running on port 8000.
- Still pending: remaining scenario command catalog and live template coverage,
  multi-step writes across authorization, multi-user enterprise verification,
  and shared HTTPS/mobile deployment validation.

Website-level live check: POST `/api/v1/chat` as existing admin, query
`我有哪些群呢`, session `enterprise-group-live-20260908`, returned HTTP 200,
`success: true`, and a readable numbered list of 10 groups. No messages or
invitations were sent. The execution plan metadata duplicated the command entry;
inspect plan aggregation separately before claiming all result presentation is fixed.

## Base and scenario policy verification (2026-09-08)

- Added and published 12 Base user scopes: app create; table read/create/update/delete;
  field read/create/update; record read/create/update; and view write-only. The
  administrator role bundle now contains 40 unique scopes including
  `offline_access`. Incremental consent requested the missing scopes and returned
  a refresh credential; the stored grant covers the full bundle.
- A real `base +base-create --as user` call created
  https://ucnn92h1qs1c.feishu.cn/base/Anw6bMMIfa4PORsWoOmcIAg2nhe.
- A real `drive +import --type bitable --as user` call imported
  `design/scenario-test-data.csv` into
  https://ucnn92h1qs1c.feishu.cn/base/Gj49bEtSAa66QRsEb1Wcj6etntb.
  Independent table and record reads recovered both populated CSV rows. Eight
  blank platform-default rows were not deleted.
- `SCENARIO_CAPABILITIES` enables 18 reviewed built-ins for admin/lead. The
  explicit `SCENARIO_UNREVIEWED` set contains the other 32. Employee access is
  17/50 because `recommend_group_meeting_times` requires lead-only group creation.
  New or unreviewed templates fail closed before render; command enforcement
  remains a second boundary during execution.
- Live API checks returned 18 ready / 32 unavailable for admin and 17 / 33 for
  employee. An unreviewed render returned HTTP 403. Real Chrome rendered all 32
  unavailable badges and disabled the selected template's controls.
- Validation: 127 backend tests passed, focused Ruff checks passed, frontend build
  passed, all 13 browser-harness groups passed, and `git diff --check` passed.
  See `design/enterprise-scenario-policy.md` for the auditable mapping snapshot.

Remaining boundary: the 32 advanced templates are intentionally unavailable,
and a physical phone/shared-HTTPS deployment has not been exercised. Localhost
cannot demonstrate phone access because `127.0.0.1` resolves to the phone itself.
