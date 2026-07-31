# Task 2 Report: Prompt OS readiness and generation hard gate

## Scope

- Backend only: `platform_app/services.py`, `platform_app/views.py`, and focused backend tests.
- No model or migration change.
- Reused Task 1 `_effective_config`, preparation revision, configuration signature inputs, and `_persist_prompt_terminal`; no parallel configuration system was added.

## RED / GREEN evidence

| Behavior | RED evidence | GREEN evidence |
| --- | --- | --- |
| N1/N2 strict normalization | Focused tests failed with missing `_normalize_n1_observation` / `_normalize_n2_identity`; existing code accepted incomplete fields and out-of-cluster references. | `test_n1_and_n2_normalizers_require_identity_fields_and_cluster_owned_references` passed. |
| N3–N6 keys, types, references, length, and N5 diversity | Focused tests failed with missing node normalizers; duplicate five-field marketing signatures and unknown references were accepted. | `test_n3_to_n6_normalizers_reject_unknown_refs_overlong_prompts_and_duplicate_marketing_sets` passed. |
| Configuration wait and identity reuse | Initial test ended `failed` because the old worker called N3 without platform/market. | `test_prompt_worker_waits_for_configuration_then_reuses_current_identity` passed: first run persisted only N1/N2 and blocked for configuration; settings requeue reused N1/N2 and ran N3/N4. |
| ERP/visual identity conflict | Initial test ended `failed` after attempting N3 instead of blocking the non-empty ERP name. | `test_prompt_worker_blocks_erp_name_when_n2_reports_visual_identity_conflict` passed. |
| One repair, current-product isolation | Existing malformed JSON test observed one repair; double-invalid test left the sibling product pending. | Full `tests/test_prompt_os.py` passed. |
| Missing/stale PromptVersion hard gate | Missing, stale-revision, stale-config, wrong-node, missing-N7, and semantic-pass-with-hard-block cases initially created a Generation or did not raise. | `test_ensure_cluster_generations_cannot_compile_a_missing_prompt_version` and all cases of `test_ensure_cluster_generations_requires_current_n4_and_passing_n7` passed. |
| N2-approved reference ordering | Initial marketing reference list put source images before the white result and retained all supporting images. | `test_generation_references_follow_n2_white_and_marketing_order` passed: white uses primary + max three supports; marketing uses white first + primary only. |
| Per-product async generation API | Existing endpoint returned 200 or aborted the whole request with 400 on one non-ready product. | Project API tests passed with HTTP 202 and per-product `queued`, `waiting`, or `blocked`; pending/preparing products persist `auto_generate`. |
| Legacy production bypass | `/api/projects/<id>/confirm/` initially returned 200 and created generic generations before Prompt OS readiness. | `test_legacy_confirm_endpoint_cannot_bypass_prompt_preparation` passed after routing it through the same async project generation path. |
| Auto-queue after readiness | Prompt worker test now requests `auto_generate=True`. | It passed with only the approved white hero queued after N1–N7 readiness. |

## Implementation

- Added strict N1–N6 normalization and deterministic N7 evidence. Parse or schema failure receives one repair attempt; a second failure terminates only the claimed product.
- Added identity input signatures based on product version/content plus cluster-owned asset IDs/hashes/roles/order. Market-only changes preserve and reuse N1/N2.
- Persisted preparation revision and effective-configuration signature in each PromptVersion input/source/structured snapshot and N7 evidence.
- Stopped after N2 when effective platform/market is not configured, using `BLOCKED` plus structured `analysis_snapshot.readiness`.
- Removed the generic `compile_slot_prompt` fallback from `ensure_cluster_generations`.
- Changed generation locking to Batch → Cluster and required current N4/N6 plus passing deterministic N7 before creation.
- Restricted generation references to N2-approved owned assets; competitor data never enters the reference array.
- Added per-product asynchronous generation responses and isolated failures. The legacy confirm endpoint now delegates to the same path.

## Verification

- Focused backend regression: `pytest -q tests/test_prompt_os.py tests/test_generation_queue.py tests/test_views.py`
- Full backend suite: `pytest -q`
- Django system check: `python manage.py check`
- Migration drift: `python manage.py makemigrations --check --dry-run`
- Diff hygiene: `git diff --check`

Final command results (2026-07-31):

- `pytest -q`: 235 passed; warnings only.
- `python manage.py check`: exit 0, no issues.
- `python manage.py makemigrations --check --dry-run`: exit 0, no changes detected. Django emitted the expected local PostgreSQL-unavailable history-check warning.
- `git diff --check`: exit 0; line-ending notices only.

## Risks / follow-up

- `confirm_generation()` remains as a legacy service used by older unit tests, but no production HTTP endpoint calls it; both project generation HTTP routes use the hard-gated asynchronous path.
- APIMart schema repair quality still depends on the provider returning the requested complete node contract on its single repair attempt; failure is fail-closed for that product.
- No frontend or project documentation was changed because Task 2 explicitly restricted those files; the accepted brief and existing Prompt OS specification already describe the target contract.

## Fix round 1 (2026-07-31)

### Review findings closed

- Added one fail-closed submission validator immediately before the provider POST. It re-reads the current Batch, Cluster, Generation, PromptVersion, same-slot N7 evidence, final references, template content, rule bundle content, preparation revision, cluster version, and identity-input signature.
- Added compare-and-set claims for Prompt preparation and Generation submission. Prompt claims bind the entire prior analysis snapshot; Generation claims allow only one `queued -> submitting` winner.
- Moved final N7 creation after the exact paid prompt and reference array are known. White images use the N2 primary plus up to three N2 supports; marketing images use the current white result first and the N2 primary second.
- Made same-slot N7 evidence content-addressed and bound it to the exact prompt, final references, structural asset, template/rule content hashes, and current preparation lineage. Forged, stale, cross-slot, or mutated evidence now fails closed.
- Asset merge, split, move, promotion, and removal invalidate preparation while preserving `auto_generate`. Stale completed white images and stale source passthrough attempts are ignored; replacement attempts append history.
- Real mode now requires a published N1–N9 node template. Fake mode uses explicit node-specific test templates. JSON repair preserves the original system instruction and node input; N1 repair also reuses the same image evidence.
- N1/N2 require non-empty identity content when continuing. N5/N7 enforce per-field marketing diversity rather than only rejecting identical five-field tuples.
- Follow-up retry, regenerate, N8, and N9 paths lock Batch → Cluster → Generation, validate the source and chosen PromptVersion under lock, and append immutable attempts. The unused in-place PromptVersion replacement bypass was removed.
- Project generation API isolates database/runtime errors per product and counts only newly queued work.

### RED / GREEN evidence

- Initial focused RED group: 8 failures covering non-empty N1/N2, N5 diversity, missing real templates, repair context, `auto_generate`, prompt CAS, and asset invalidation; the same 8 tests passed after implementation.
- Submission RED group: 8 failures covering forged N7, template/rule mutation, source bypass, legacy worker submission, Generation CAS, API idempotent count, and per-item database isolation; all passed after implementation.
- Follow-up RED group: 4 failures covering N8/N9 lineage, legacy regeneration, and lock order; all passed after implementation.
- Focused backend regression: 105 passed.
- First full regression exposed 8 legacy-contract tests; they were updated to use real gated PromptVersions or to assert fail-closed legacy behavior. All 8 passed after migration.

### Final verification

- `pytest -q`: 255 passed; warnings only.
- `python manage.py check`: exit 0, no issues.
- `python manage.py makemigrations --check --dry-run`: exit 0, no changes detected; only the expected local PostgreSQL-unavailable history-check warning.
- `git diff --check`: exit 0; line-ending notices only.

### Documentation impact

- No project authority document changed. The existing Prompt OS specification already requires N7 before every paid image execution, immutable PromptVersion/Generation history, final-reference traceability, and per-product isolation.
- This append-only task report is the review-fix handoff required by the accepted task boundary.

## Fix round 2 (2026-07-31)

### Review findings closed

- **C1 exact reference authorization:** submission now resolves the current N2 primary and at most three current N2 supporting image relations from the same product. White generations require that exact ordered set; marketing generations require the exact current completed white result followed by the current N2 primary. Cross-product, competitor, historical, missing, reordered, or additional paths fail closed, and same-slot N7 is bound to the same exact references and current white generation.
- **C2 TOCTOU sealing:** the worker claims and then locks Batch → Cluster → Generation, revalidates the complete paid request, and persists an immutable content-addressed submission fingerprint. It repeats the same locked validation immediately before the provider POST, while supported preparation/configuration and node-publication mutation paths reject changes during an active sealed submission. Database locks are released before network I/O.
- **I1 N9 transactionality:** N9 provider optimization remains outside the database lock, while its N7 record, Cluster analysis update, PromptVersion, and follow-up Generation are created together inside the Batch → Cluster → Generation transaction. A competing/newer attempt causes the whole local write set to roll back.
- **I2 terminal regenerate source:** follow-up generation accepts only completed, failed, or canceled source attempts. Queued, preparing, submitting, submitted, processing, archiving, and submit-unknown sources are rejected.
- **I3 strict N1/N2 schema:** N1 now enforces the owned-image role, integer visibility/quality bounds, string category candidates, and non-empty observed shape. Continuing N2 output now requires typed `must_not_change`, category, and primary appearance fields in addition to current owned primary/supporting assets.
- **I4 complete bindings:** template/rule snapshots now include publication status and execution defaults/source metadata. PromptNodeTemplate bindings include node/version/status plus a hash of the exact instruction and output schema and must remain published. N7 and PromptVersion snapshots bind the actual image model, size, resolution, and fixed generation parameters used by the request.
- **M1 created-count ownership:** `ensure_cluster_generations(..., include_created=True)` returns the exact IDs created by that locked service call, and the API derives `generation_count` only from those IDs rather than a before/after query race.
- Generation model/queryset guards prevent an existing sealed submission fingerprint from being replaced or removed while allowing later provider status fields to be merged.

### RED / GREEN evidence

- Added 25 directed review tests spanning exact white/marketing reference rejection, template/rule/node/request mutation, sealed interleaving, supported configuration invalidation, fingerprint immutability, terminal-source enforcement, N9 rollback, strict N1/N2 types, and current-call creation ownership.
- RED: all 25 new directed tests failed against the round-1 implementation.
- GREEN: all 25 directed tests passed after the minimal backend changes.
- Focused backend regression:
  `pytest -q tests/test_prompt_os.py tests/test_generation_queue.py tests/test_views.py tests/test_upload_clusters.py tests/test_project_configuration.py tests/test_review_delivery.py`
  — 165 passed.

### Final verification

- `pytest -q`: 280 passed; warnings only.
- `python manage.py check`: exit 0, no issues.
- `python manage.py makemigrations --check --dry-run`: exit 0, no changes detected; only the expected local PostgreSQL-unavailable migration-history warning.
- `git diff --check -- platform_app/models.py platform_app/services.py platform_app/views.py tests/test_generation_queue.py tests/test_prompt_os.py`: exit 0; line-ending notices only.

### Documentation impact and remaining risk

- No product authority document or frontend file changed in this backend review fix. The current requirements boundary, phased roadmap, dual-speed design, and Prompt OS node contract already require exact N2 references, request-bound same-slot N7 evidence, immutable snapshots, and revalidation immediately before every paid POST.
- This append-only task report and the weekly work log are the durable handoff artifacts for round 2.
- Verification used fake/local provider and storage paths only. No production call, deployment, credential, OSS, ERP, or external data write was performed.

## Fix round 3 (2026-07-31)

### Review findings closed

- **C2 final POST serialization:** the complete provider `submit_generation()` call, including APIMart reference uploads and the generation POST, now runs inside one `transaction.atomic()` block. It locks Batch → Cluster → current Generation, then the current white-result rows, all current ClusterAsset/Asset rows, OutputTemplate/OutputSlot, all potentially applicable RuleProfile rows, and every version row for the active PromptNodeTemplate node. The immutable request fingerprint is revalidated only after those locks are held.
- Independent mutation transactions for Batch settings, direct Cluster snapshot clearing, Asset paths, OutputTemplate defaults, RuleProfile rules, and PromptNodeTemplate instructions are therefore blocked until the provider call returns. Changes committed afterwards invalidate only later requests.
- Provider exceptions leave the lock transaction before existing `failed` or `submit_unknown` state handling runs, so error-state writes do not self-deadlock and the lock transaction rolls back cleanly.
- **I3 authoritative N1/N2 schema:** owned-product N1 now requires `asset_kind=owned_product`, boolean `target_is_physical_product` and `target_complete`, enumerated `background_complexity`, and `recommended_use=reuse`, in addition to the prior strict identity fields. N2 now requires typed `needs_input_reason`, `standardization_mode=reuse`, and `standardization_reason`; `needs_input` requires a non-empty reason. Existing one-repair behavior remains fail closed after the second invalid payload.

### RED / GREEN and verification

- RED: 17 directed cases failed—six relevant row mutations completed inside the old client call, and 16 missing/wrong-type/wrong-enum N1/N2 payload variants were accepted.
- GREEN: all 17 directed cases passed after the two scoped fixes.
- Focused regression: `pytest -q tests/test_prompt_os.py tests/test_generation_queue.py` — 112 passed.
- Full backend regression: `pytest -q` — 297 passed; warnings only.
- `python manage.py check`: exit 0, no issues.
- `python manage.py makemigrations --check --dry-run`: exit 0, no changes detected; only the expected local PostgreSQL-unavailable migration-history warning.
- Scoped `git diff --check`: exit 0; line-ending notices only.

### Ceiling and documentation impact

- The provider upload/POST now intentionally holds a database transaction and row locks across network I/O. This is the accepted preview-stage correctness ceiling; if throughput later requires removing the long transaction, the upgrade path is an externally serialized submission/idempotency protocol with equivalent mutation exclusion.
- No frontend or product authority document changed. The current Prompt OS specification is the authoritative source for these N1/N2 fields and already requires final request revalidation immediately before the paid POST.
- No production call, deployment, credential, OSS, ERP, or external data write was performed.
