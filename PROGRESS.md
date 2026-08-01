# Historical SKU entry and eight-slot baseline

- Status: completed backend slice on 2026-07-29; SKU import now creates private product cards, exposes an eight-slot plan and public preflight summary, and preserves historical templates and generation snapshots.
- Contract: `POST /api/projects/<batch_id>/sku-import/` accepts `{"skus": ["..."]}` and returns per-request imported/failed counts. Catalog data is limited to SKU, product name, and picture; failures are recorded per SKU without exposing source URLs or provider data.
- Safety: catalog tokens stay in process memory; image downloads allow only exact configured public IPv4/IPv6 literals, revalidate every redirect, and enforce MIME, byte, and pixel limits before private asset archival. Network I/O happens before per-SKU database transactions.
- Verification: 123 pytest cases passed; `manage.py check`, migration drift check, and `git diff --check` passed on 2026-07-29.

- Third review follow-up: SKU import responses now expose one sanitized item per input SKU, and project snapshots expose `skuImports` from each SKU's latest audit record, including failed imports without a product card. No source URL, provider response, or token is serialized.
- Verification: focused SKU/template tests and the full pytest suite passed; `manage.py check`, migration drift check, and `git diff --check` passed on 2026-07-29.

- Review follow-up: catalog image decoding and pixel validation now finish before any per-item transaction, so a bad or oversized image creates a failed audit item while later SKUs continue. Download responses are closed on every path and reject disallowed redirect targets, MIME mismatches, declared lengths, and streamed byte overflow. Stable seed-key selection now prefers the published eight-slot baseline/upgrade templates over custom global templates.
- Verification: superseded by the final 109-test verification below.

- Second review follow-up: template fallback now deterministically prefers the upgrade eight-slot seed before the baseline seed when both exist. Catalog image URLs accept HTTP or HTTPS only for exact configured hosts, resolve every initial and redirect host, and reject every non-global address even if allowlisted. SKU import rejects non-object JSON with a controlled 400 response.
- Verification: 109 pytest cases passed; `manage.py check`, migration drift check, and `git diff --check` passed on 2026-07-29.

- Fourth review follow-up: every SKU import audit now receives a strictly increasing `attempt` within its batch and SKU under the existing batch row lock. A database uniqueness constraint prevents duplicate attempts, and project snapshots select the latest audit by attempt instead of timestamp or random UUID.
- Migration: `0009_sku_import_attempt` keeps every historical audit, deterministically backfills attempt order, then enforces non-null attempts and uniqueness. The response and snapshot payloads remain sanitized and do not expose source URLs, tokens, or provider responses.
- Verification: the forced equal-`created_at` regression passed, all 16 SKU import tests and all 110 pytest cases passed, a clean database migrated through `0009`, an `0008` database with audit rows upgraded and retained them in attempt order, `manage.py check`, migration drift, and `git diff --check` all passed.

- Fifth review follow-up: batches with any confirmation key or generation history now return a per-SKU `project_locked` audit without querying/downloading or creating product cards; the same Batch row lock is checked again immediately before each write, serializing safely with confirmation.
- Catalog image URLs now accept only exact allowlisted public IPv4/IPv6 literals on the initial URL and every redirect. Hostnames, credentials, private, loopback, link-local, reserved, and unlisted addresses are rejected without DNS resolution. The current `.env.example` documents the confirmed ERP login endpoint at `103.198.125.2:16777` and image allowlist `180.167.156.35`; production credentials remain outside Git.
- Import requests default to at most 50 input entries. The catalog is queried once with `skuList`, then each SKU runs download/validation outside a transaction followed by its own short archive/audit transaction, so image bytes are not retained for the whole batch. Catalog-wide failures use sanitized `catalog_unavailable`; true empty results remain `sku_not_found`.
- Local archive `OSError` creates a sanitized failure for only that SKU and continues. Partial writes and files whose database transaction later fails are removed.
- Verification: all 29 SKU import tests and all 123 pytest cases passed; `manage.py check`, `manage.py makemigrations --check --dry-run`, and `git diff --check` passed on 2026-07-29.

- Sixth review follow-up: database persistence failures now roll back only the current SKU, remove its local archive, record a sanitized `archive_failed` audit in a fresh short transaction, and continue later SKUs. Exact-allowlisted image URLs additionally reject IPv4/IPv6 multicast and every other non-unicast-public literal. Catalog login/query responses now require an object envelope with explicit success, no non-200 code or error status, and correctly typed `data`; malformed or failed envelopes become sanitized `catalog_unavailable`, while only an explicitly successful empty product list becomes `sku_not_found`.
- Verification: all 43 SKU import tests and all 137 pytest cases passed; `manage.py check`, `USE_SQLITE_FOR_TESTS=1 manage.py makemigrations --check --dry-run`, and `git diff --check` passed on 2026-07-29.

- Seventh review follow-up: Catalog login now requires a nonblank string Token, and a nonempty query list containing a non-object, missing SKU, or blank SKU is a catalog failure rather than a false `sku_not_found`. Valid catalog entries for SKUs not requested are ignored.
- Verification: all 48 SKU import tests and all 142 pytest cases passed; `manage.py check`, `USE_SQLITE_FOR_TESTS=1 manage.py makemigrations --check --dry-run`, and `git diff --check` passed on 2026-07-29.

- QA/release local Task 6-7 slice: platform admins from `PLATFORM_ADMIN_ERP_USERS` now become Django staff; admin-only template/rule/model/usage controls are hidden from ordinary staff users; non-global rule publishing in admin requires source URL, site, checked date, and version. Global seed is now the 1+8 nine-slot baseline, with legacy eight-slot templates preserved for historical batches.
- APIMart smoke: added `python manage.py smoke_apimart_nodes`, which creates a local Pillow test image and exercises vision, prompt, and image nodes through fake mode or the configured APIMart client. Output is limited to node status, elapsed milliseconds, and SHA-256 hash; fake/empty/replacement keys are rejected in real mode without echoing the key.
- Verification: focused auth/template/APIMart tests passed locally; Django check, migration dry-run, and final diff checks are recorded in the committing task output.

# Dual-speed frontend workspace

- Status: completed frontend Task 5 on 2026-07-30. The React app now uses a unified project workspace at `/projects/:id` and project results at `/projects/:id/results`; `/review` is no longer routed.
- Contract: upload and ERP SKU import both send explicit `mode: auto|organize`; project generation sends selected cluster IDs and slot orders 1–9; result export posts selected generation IDs; revise/regenerate use generation-scoped endpoints.
- Remaining dependency: integration E2E/browser smoke must verify the backend `/generate/`, `/regenerate/`, `/revise/`, and selected `/export/` contracts with the new workspace.
- Verification: `npm --prefix frontend test` passed with 41 tests; `npm --prefix frontend run build` passed on 2026-07-30.

# Real preview deploy and paid 1+8 smoke

- Status: deployed commit `ce7da6b` to `hermes-remote:/opt/independent-image-platform` on 2026-07-30 under `global.lock`.
- Contract fixes: Prompt JSON repair, PostgreSQL row locks without nullable joins, automatic slots 2–9 enqueue after white-background slot completion, and export gate requiring `review_status=accepted`.
- Smoke: APIMart DeepSeek/GPT-5 Nano/GPT Image 2 three-node smoke passed; OSS write/read/delete passed; one real paid 1+8 batch completed 9/9 results, then 9 accepted images exported to a local ZIP.
- Verification: backend 179 passed, frontend 41 passed, Vite build passed, Django check passed, remote migration dry-run passed, HTTP/DOM smoke passed. ERP login endpoint is reachable; real employee login/SKU import still needs browser or dedicated smoke account validation.

# ERP login page polish

- Status: deployed commit `b7ac583` to `hermes-remote:/opt/independent-image-platform` on 2026-07-30 under `global.lock`.
- Change: login now presents a polished ERP account card, explains that first successful ERP login auto-creates the platform shadow account, and keeps ERP password out of platform storage.
- Verification: focused login page test failed before the template change and passed after; full backend suite passed with 180 tests; remote Django check, health check, and login page smoke passed.

# Legacy batch UI removal

- Status: deployed commit `8b253b7` to `hermes-remote:/opt/independent-image-platform` on 2026-07-30 under `global.lock`.
- Change: deleted the original Django batch list/form/detail templates and legacy static JS. `/batches/`, `/batches/new/`, and `/batches/<id>/` now only perform authenticated compatibility redirects to React routes.
- Verification: regression tests first reproduced the old page behavior, then passed after the deletion; backend 181 passed, frontend 41 passed, Vite build passed, remote health passed, and remote legacy redirect smoke passed.

# Prompt OS v2 P0 local integration

- Status: local implementation completed on 2026-07-30; remote preview still runs the earlier deployed version.
- Change: usable image/folder/drag upload, per-file retry, official platform rule bundles, Shopee VN source-photo exception, nine-node Prompt OS, inference ledger, structured PromptVersion editing, review revision and failure simplification are integrated.
- Prompt templates: N1–N9 publish complete `2.1.0` core instructions; DeepSeek receives the complete node template as the system message. The retired `2.0.0` one-line summaries remain immutable for audit.
- Verification: backend 192 passed, frontend 45 passed, Django check, SQLite migration drift check, Vite build, and `git diff --check` passed.
- Remaining: browser real folder upload, remote OSS/APIMart smoke, deployment, real ERP employee login/SKU import, and the six-category 54-image quality benchmark.

# Prompt OS v2 P0 preview deployment

- Status: Git `85c61dd` deployed to `hermes-remote:/opt/independent-image-platform` on 2026-07-30 under `global.lock`.
- Verification: migration 0011 applied; nine published Prompt OS `2.1.0` templates each exceed 300 characters; Web, PostgreSQL, Prompt Worker, Generation Worker and Caddy are running; health, login page, React shell and upload-control static readback passed.
- Real prompt smoke: DeepSeek accepted the published N3 system instruction (`562` characters) and returned a non-empty response recorded only by SHA-256.
- Backup: `/opt/independent-image-platform-backups/20260730_192014`; the deployment lock was released.
- Remaining: browser real folder upload, real ERP employee login/SKU import, and six-category 54-image quality benchmark.

# Real upload, product confirmation and N1-N7 preparation

- Status: runtime Git `3966773` deployed to `hermes-remote:/opt/independent-image-platform` on 2026-07-30 under `global.lock`.
- Upload smoke: authenticated multipart PNG/WebP/TXT import passed; WebP normalized to PNG, TXT seed applied, two clusters created, OSS objects read back and permission-checked preview returned.
- Product flow: second image merged into the first cluster; `name` and `same_product` now persist, PostgreSQL row locking no longer joins the nullable template, and editing a blocked product requeues preparation.
- Prompt flow: confirmed product names override N2 low-confidence blocking; N5 accepts documented and APIMart-observed envelopes and performs at most one schema repair. The real two-reference project reached `ready` with 23 N1-N7 snapshots and 9 PromptVersion rows without starting image generation.
- Verification: backend 193 passed, frontend 45 passed, Vite build, Django check, migration drift, migration 0012, five running services and `/health/ready` passed.
- Remaining after this milestone: real ERP employee login/SKU import, six-category 54-image benchmark, HTTPS and staged concurrency.

# Phase 1 batch organization workspace

- Status: Git `d5410bb` deployed to `hermes-remote:/opt/independent-image-platform` on 2026-07-31 under `global.lock`.
- Change: pending image/folder previews no longer expose filenames; successful uploads clear while failed items remain; product selection supports all/none/invert; compact rows use a right-side detail drawer; empty names stay blank; unused products/assets delete while historical ones archive.
- Safety: archived products are excluded from preparation, generation, review and export. Prompt preparation and active/uncertain generation block structural edits or deletion. Automatic import waits for Prompt Worker completion instead of posting generation from stale frontend state.
- Verification: backend 206 passed; frontend 51 passed; Vite build, Django check, migration drift, `git diff --check`, single-image browser E2E and native folder picker E2E passed.
- Deployment: migration `0013` applied; Web, PostgreSQL, Prompt Worker, Generation Worker and Caddy are running; local and public `/health/ready` passed. Backup: `/opt/independent-image-platform-backups/20260731_112241`; the deployment lock was released.
- Remaining: real employee ERP SKU/OSS browser smoke; upload request idempotency for lost acknowledgements; later roadmap phases.

# ERP image download, logout, and explicit image pickers

- Status: deployed on 2026-07-31 as Git `27a580f`; the full employee-browser SKU → preview → OSS smoke remains pending.
- Change: the existing ERP image download path remains allowlisted and session-scoped; `POST /logout/` now requires a CSRF token, accepts only Django's followed redirect to `/login/`, clears the Django and ERP-token session, and surfaces logout failures without leaving the current page. Upload UI now names the native single/multiple image picker and separate whole-folder picker explicitly while retaining its existing deduplication and import path.
- Configuration: `.env.example` records the confirmed ERP login endpoint `http://103.198.125.2:16777/open/system/innerOpen/login` and exact image-source allowlist `180.167.156.35`; production was updated under `global.lock`. Credentials and runtime tokens remain unset and untracked.
- Verification: backend 207 passed; frontend 56 passed; Vite build, Django check, migration drift and `git diff --check` passed. Production reports release `27a580f`, five services healthy, public `/health/ready` OK, all three UI labels present, logout clears both auth and ERP-token session state, and a real ERP JPEG downloads through the allowlisted application path.
- Rollback: `/opt/independent-image-platform-backups/20260731_121223-erp-logout-picker`.

# Explicit preparation workbench and Prompt OS v3

- Status: Git `bd7293d` deployed to `hermes-remote:/opt/independent-image-platform` on 2026-07-31 under `global.lock`.
- Change: organize import now performs zero AI calls; explicit preparation runs N1–N7 and builds the identity card plus 1+8 prompts; formal generation requires the current N7-approved PromptVersion. The React workspace now uses compact Chinese project controls, square product cards, inline details, and drag merge/move/split.
- Prompt: published the shared fact chain and generic/Shopee/TikTok marketing variants from Prompt OS v3; final image prompts remain capped while full node system prompts are retained.
- Verification: backend 335 passed, frontend 83 passed, Vite build passed; remote migration `0015`, template seed, Django check, five services and `/health/ready` passed.
- Rollback: `/opt/independent-image-platform-backups/20260731_195703-bd7293d`.
- Remaining: real employee ERP SKU/OSS browser smoke and a new paid 1+8 operator quality review.

# Workbench v4 implementation

- Status: started 2026-08-01 on `lxc/workbench-v4` from `e98ad9f`.
- Goal: persistent import, large product cards, ordered real thumbnails, a fixed non-reflowing side panel, and one shared 1+8 set for every image in a product card.
- Completed: read project rules and six authoritative documents; updated the confirmed boundary, design, Prompt OS contract, roadmap, leader goal, STATUS, and CLAUDE.
- Next: reproduce the known backend/frontend failures, update behavior tests first, then implement the smallest compatible API, Prompt OS, and React changes.
- Reuse: `ClusterAsset`, `analysis_snapshot`, existing APIs, TanStack Query, and dnd-kit; no new table, state store, or frontend dependency.
- Risk: preserve historical single-appearance snapshots while adding `target_appearances` and per-slot `appearance_ids`.
- Forbidden: do not touch the untracked user-operation-flow document, secrets, or historical Prompt/generation/review records.
