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
