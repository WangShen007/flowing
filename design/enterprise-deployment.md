# Enterprise deployment configuration

Use one company-owned Feishu app with employee availability configured by the
company administrator. Website roles do not expand Feishu resource access.

## Server configuration

Configure these environment values on the server, outside version control:

- `FEISHU_APP_ID`: the shared company application ID.
- `FEISHU_APP_SECRET`: that application's secret.
- `FEISHU_TENANT_KEY`: verified tenant key from Feishu user_info, not a display name.
- `FEISHU_REDIRECT_URI`: exactly the website login URL, such as
  `https://work.example.com/login`. Register the exact value in Feishu security settings.
- `FEISHU_TOKEN_ENCRYPTION_KEY`: base64-encoded random 32-byte key. Keep it stable
  across restarts and shared by all workers. Back it up separately from the database.
- `AUTH_SESSION_DAYS`: website device-session lifetime, default 30 days.
- `FEISHU_CLI_DATA_DIR`: optional persistent data directory; defaults to
  `.feishu_cli_data` in the repository. It contains SQLite, profile isolation
  directories, refresh locks and enterprise.env. All workers must share it.

The local test callback is `http://127.0.0.1:8000/login`. It does not provide mobile
access: on a phone, 127.0.0.1 means the phone itself. Multi-device use requires one
reachable HTTPS origin and the same server database. Never create separate per-device
databases or copy personal credentials between employees.

The local test service now loads company configuration from the gitignored
`.feishu_cli_data/enterprise.env`. Preserve its existing encryption key. Real
OAuth account reuse, v3 refresh rotation and an enterprise group query passed
on 2026-09-08. New-device login requests identity/continuous access first;
business authorization is reused from the verified account. Feishu may still
show a confirmation screen. Sessions expire after the configured duration;
revocation or expired refresh credentials require reconnection.

Authorization-code exchange uses the v2 token endpoint because the current
authorize endpoint documents that PKCE compatibility requirement. Refresh uses
v3. Revalidate the provider's compatibility before migrating code exchange.
Every incremental consent request retains offline_access so the replacement
grant includes a refresh token, even when only business scopes were missing.

## Application scopes

Review `backend/app/core/feishu_permissions.py` for role bundles and mapped commands.
Enable corresponding user scopes in the Feishu app, including `offline_access`,
and publish required configuration changes before testing employee consent.
Do not use bot scopes for commands that act as the employee.

Employees receive ordinary document, message, calendar and task capabilities;
project leads additionally receive project-group and task-list creation capabilities.
Administrators manage website members and global settings. Enterprise CLI commands
outside the reviewed catalog are rejected until added with verified scope mapping.

All 50 built-in templates have an explicit enterprise decision. Admin/lead users
currently have 18 reviewed templates; employees have 17 because project-group
creation is lead-only. The other 32 templates show `企业未开放` and are rejected
before prompt rendering or execution. Keep
`design/enterprise-scenario-policy.md` and its coverage test synchronized when
adding templates. A reviewed policy means its possible commands have scope and
role mappings; it does not by itself prove a live end-to-end run.

Consult `CAPABILITY-VERIFICATION-MATRIX.md` and the current scope-contract tests for
the role bundles; app-side permission and individual consent are separate. The local test app's Base permissions and
incremental consent passed real create/import/read verification on 2026-09-08.

## Migration and verification

Keep the existing SQLite database and CLI user directories. Sign in to the existing
website account before connecting its verified Feishu identity, so old conversations
remain associated with the same account. Never merge accounts by name.

Verify two independent browser sessions map to the same account and separate people
map to separate accounts. The same-account/two-session case has passed locally;
the different-person case still requires two real employee identities. Test
consent denial, stale callback, missing scopes, role
downgrade, revoked refresh credentials and disabled scheduled-task owners. Verify
real group queries and a clearly labelled test document before enabling other workflows.

Disconnect stops local use immediately and attempts Feishu remote revocation.
When remote revocation fails, the encrypted credential is retained only to retry
revocation and marked unusable. Identity/history remain bound to the account.

The current refresh lock uses POSIX file locks next to SQLite. Deploy workers on
one host with the same persistent data volume. A multi-host deployment requires a
shared transactional credential store and distributed refresh locking before use.

Remaining production work includes replacing demo local credentials, enforcing
HTTPS at the reverse proxy, and moving website bearer sessions to a reviewed
cookie/CSRF design. Do not describe the current development server as production-ready.

## Snapshot and recovery preparation

`backend/data/sqlite_snapshot.py` copies one SQLite database to a **new** destination
using SQLite's backup API, including committed WAL data. It rejects any existing
destination, restricts the snapshot to mode 0600, closes its database connections,
checks integrity, and returns a SHA-256 digest without printing database contents.
It accepts explicit `--source` and `--destination` paths. Restoration is the same
copy operation into a new isolated database path, never an in-place overwrite.

Before an application-wide rollback point, stop/drain all chat and scheduler
writers. Snapshot both `feishu_cli_web.sqlite3` and `agent_checkpoints.sqlite3`
from the configured data directory as one coordinated, quiescent set. Two live
independent snapshots are not a cross-database transaction. Also securely retain
the matching `enterprise.env` encryption key/configuration, CLI profile directories,
uploaded/generated files and the matching application revision. Database snapshots
alone cannot recover encrypted credentials or PPT content. Restrict the containing
backup directory and apply the organization's encryption/retention policy.

Verify restoration in a separate data directory with scheduling disabled and no
business writes before replacing any live paths. Check account ownership and
unknown-write receipts; do not reactivate ambiguous scheduled jobs automatically.
Never assign historical unowned PPT files or lossy-normalized account data to a
user by guessing. Production retention and off-host recovery remain unverified.
