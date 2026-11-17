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
- In account mode, every queued command is submitted with an account and role stamped from server context plus a keyed proof. The worker verifies that proof before running application code, binds the account for nested queries and jobs, and rejects old unsigned jobs. A worker accidentally started in anonymous mode rejects account-stamped jobs. Job-status lookup checks the same proof and owner before returning a result. Operators must drain or explicitly handle old queued work before switching modes; the startup guard remains until all account paths, including audio and UI, are isolated.
- New podcast audio is written under the verified account's directory, and stored relative paths are resolved against that directory for streaming, listing, retry, and deletion. Only the designated legacy administrator can read audio under the original `episodes/` root. Existing single-user directory behavior is preserved outside account mode. The startup guard remains until chat checkpoints, complete UI, and deployment checks are done.
- LangGraph chat checkpoints remain in the server's SQLite store but use an account-prefixed thread key for each new account. The designated legacy administrator continues to use the original session key, retaining pre-migration messages. API routes verify the session row through account-scoped SurrealDB before reading the corresponding checkpoint. The shared SQLite file remains private to the service process, not a browser-facing data source.
- The complete Next.js interface can use a second HTTPS port on the TAgent hostname. Set `TAGENT_PUBLIC_ORIGIN` to the TAgent UI origin and `OPEN_NOTEBOOK_PUBLIC_ORIGIN` to the notebook UI origin in both application environments. TAgent accepts the notebook origin only for `/auth/*` writes; the notebook API checks its own origin and CSRF. Next.js forwards only login, registration, logout, and session reads to the loopback TAgent backend. Do not expose the two backends or the Next.js port directly to the public network.
- When a trusted HTTPS ingress strips incoming `X-Real-IP`, sets it to the real client address, and prevents direct access to Next.js, set `OPEN_NOTEBOOK_TRUSTED_PROXY=1` on the Next.js process. The auth proxy then forwards that verified address for TAgent registration/login rate limits. Without that setting, requests share the proxy's loopback address and the no-mail registration limit is 20 per hour in total.
- SurrealDB 2.7 integration checks run all 27 migrations in a fresh test namespace and verify that a student cannot read, change, delete, or search administrator notes or forge their owner. Student model-key status and import endpoints hide process-wide secrets; students must use personal credentials and external HTTPS model URLs. These checks do not replace a production HTTPS, worker, and two-browser acceptance run.
