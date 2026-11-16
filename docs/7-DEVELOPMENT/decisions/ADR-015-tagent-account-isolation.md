# ADR-015: Isolate Open Notebook by TAgent account

- **Status**: Accepted for the TAgent fork
- **Date**: 2026-11
- **Related**: PDR-001, ADR-001, ADR-004

## Context

Open Notebook was designed for one user. Its API, worker, uploads, chat checkpoints, model credentials, and default settings currently share one database and one identity. TAgent now lets students create their own email/password accounts and requires the complete Open Notebook interface to follow those accounts across devices. Existing shared content belongs only to the administrator.

## Decision

Keep one Open Notebook deployment, but give every authenticated TAgent account a separate database record identity. The API validates the server-side TAgent session and uses a short-lived, server-signed SurrealDB record token for user queries. SurrealDB row permissions enforce ownership on every personal table, including relations, search inputs, models, credentials, and settings; browser-supplied account IDs never grant access. Fixed singleton settings become per-account records. Each queued command carries an authenticated owner and runs in that owner's scope. Uploads and chat checkpoints are partitioned by owner. The initial data migration assigns all legacy content to the administrator after a backup. Public access to API, UI, worker, and database must go through the HTTPS account boundary; no shared Open Notebook password is exposed to browsers.

## Alternatives considered

- UI-only filtering: direct IDs, downloads, search, and background work would remain shared.
- One container/database per student: operational cost grows with the class and complicates upgrades and backups.
- Using root database credentials for user requests plus code filters: a missed query or new endpoint could expose other accounts.

## Consequences

- Every user-visible data class and background path needs an ownership audit and cross-account tests before account mode can be enabled.
- Old single-user deployments keep their present behavior unless account mode is explicitly enabled after migration and endpoint checks.
- Model keys remain encrypted at rest, and their records and effective model defaults are restricted to the owning account; environment-wide fallback keys must be disabled in account mode.
- Deployment needs a fixed TAgent session verifier, a protected SurrealDB signing secret, HTTPS, private backend ports, and a tested database plus uploads rollback snapshot.
- Migration 27 is available only in account mode. It requires `OPEN_NOTEBOOK_LEGACY_ADMIN_ID` to match the existing TAgent administrator and a URL-safe `OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY`. Every pre-existing personal row is assigned to that administrator before record-user permissions are enabled. New rows receive an explicit owner from verified server context. Returning to single-user mode requires restoring the pre-migration backup; the down migration intentionally refuses to remove permissions in place.
- SurrealDB record authentication requires a corresponding `account` row. The API creates that row with one narrow root-level UPSERT only after TAgent has verified the session and, for writes, Origin and CSRF. A short in-process cache limits repeated provisioning; all ordinary data queries still use the signed record identity.
- In account mode, new uploads go under an account-ID directory. File path validation resolves links and permits only that account's directory; the administrator can also access legacy files stored directly under the original uploads root. Source downloads and delete cleanup enforce this boundary. The file boundary alone is not enough to enable account mode: queued workers must carry the verified owner, and file-serving paths need deployment-level review.
- Fixed configuration records now resolve to an account-specific ID for each new account. The designated legacy administrator keeps the original IDs and settings; model defaults, content settings, default prompts, and legacy provider configuration use fresh per-request instances so cached values cannot cross accounts. Row permissions still enforce the owner on the resolved record.
