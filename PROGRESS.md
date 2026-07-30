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
- Catalog image URLs now accept only exact allowlisted public IPv4/IPv6 literals on the initial URL and every redirect. Hostnames, credentials, private, loopback, link-local, reserved, and unlisted addresses are rejected without DNS resolution. `CATALOG_ALLOWED_IMAGE_HOSTS` is empty by default/example until operators explicitly configure public IP literals.
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
