# Task 1 report — project defaults and product overrides

## Change scope

- Added nullable Cluster platform, market, and seller-tier override fields and migration `0014_cluster_configuration_overrides`.
- Changed `POST /api/projects/` to create from the project name only, ignoring legacy configuration fields and binding the global published fallback template and rules.
- Added `PATCH /api/projects/{id}/settings/` for the approved snake_case payload. It validates project settings, resolves published configuration, preserves historical rows, and requeues only products whose effective configuration changes with auto-generation disabled.
- Extended Cluster updates for nullable normalized overrides and added the approved camelCase configuration fields to project/product snapshots.
- Added focused contract tests and updated the prior project-create test to the name-only API contract.

## RED

Command:

```powershell
pytest tests/test_project_configuration.py -q
```

Observed expected failures: missing `configurationStatus`, missing Cluster override model fields, and missing `api_project_settings` route. Result: `4 failed`. Review follow-up tests also failed as expected before the final fix: a non-Shopee override resolved to `mall`, and a blank override was accepted.

## GREEN

Commands:

```powershell
pytest tests/test_project_configuration.py tests/test_review_delivery.py -q
pytest -q
git diff --check
```

Results: final focused configuration plus legacy-create coverage `8 passed`; final full suite passed; system check and diff check exited `0`.

## Migration check

Command:

```powershell
python manage.py makemigrations --check --dry-run
```

Result: exit `0`, `No changes detected`. Django also emitted the existing local PostgreSQL connection warning while checking migration history; the drift check itself passed.

## Risks and remaining work

- No remaining Task 1 implementation items.
- Cluster effective configuration is now the persisted/API preparation contract. Prompt/generation workers still consume the project template/rules until their assigned follow-up task adopts product-effective configuration.

## Fix round 1/5

### Changes

- Added a preparation revision in `analysis_snapshot` for settings/override invalidation while preserving prior analysis and prompt history.
- Prompt workers now abandon stale claimed revisions instead of finalizing stale prompts, READY, or FAILED states.
- Legacy `site` is used when `market` is empty for configuration serialization/status.
- Settings now allow only verified platform (`shopee`, `tiktok`), size (`1:1`, `3:4`), and resolution (`1k`, `2k`) values.
- Template/rule IDs are included in internal invalidation signatures without changing the public effective-config schema.

### RED

```powershell
pytest tests/test_project_configuration.py::test_site_only_project_uses_legacy_market_for_configuration_snapshot tests/test_project_configuration.py::test_project_settings_rejects_unsupported_verified_values tests/test_project_configuration.py::test_settings_requeues_when_resolving_template_or_rule_changes -q
pytest tests/test_prompt_os.py::test_prompt_worker_requeues_when_settings_change_during_preparation -q
```

Observed failures: legacy site-only rows returned `required`; unsupported settings returned `200`; template/rule rebinding left a product `ready`; and a settings change during prompt preparation finished `ready`.

### GREEN

```powershell
pytest tests/test_project_configuration.py::test_site_only_project_uses_legacy_market_for_configuration_snapshot tests/test_project_configuration.py::test_project_settings_rejects_unsupported_verified_values tests/test_project_configuration.py::test_settings_requeues_when_resolving_template_or_rule_changes tests/test_prompt_os.py::test_prompt_worker_requeues_when_settings_change_during_preparation -q
```

Result: `6 passed`. Final `pytest -q` passed after updating the older configuration test to expect template/rule rebinding invalidation.

## Fix round 2/5

- Terminal prompt persistence now locks the Cluster and verifies the claimed revision before creating any PromptVersion rows or setting READY/BLOCKED/FAILED; stale work leaves the newer claim untouched.
- RED: the prior interleaving test demonstrated stale completion (`ready`) after invalidation. GREEN: `pytest tests/test_prompt_os.py::test_prompt_worker_requeues_when_settings_change_during_preparation tests/test_project_configuration.py -q` returned `13 passed`.

## Fix round 3/5

- Extracted `_persist_prompt_terminal`, which locks only Cluster, compares revision/status, then atomically creates all prompt versions and writes the terminal state.
- RED: direct stale rev1 persistence import failed before the helper existed. GREEN: `pytest tests/test_prompt_os.py::test_stale_terminal_persistence_cannot_overwrite_a_newer_claim tests/test_prompt_os.py::test_prompt_worker_requeues_when_settings_change_during_preparation tests/test_project_configuration.py -q` returned `14 passed`.

## Fix round 4/5

- `process_prompt_once()` now returns immediately when `_persist_prompt_terminal()` rejects a stale revision, before refreshing the cluster or considering auto-generation.
- Added an end-to-end stale terminal regression that installs a newer revision-2 `PREPARING` claim with `auto_generate=True` while revision 1 is running, and verifies generation scheduling is not called and no `PromptVersion` or `Generation` rows are created.
- RED: `pytest tests/test_prompt_os.py::test_prompt_worker_stale_terminal_does_not_autogenerate_newer_claim -q` failed because `ensure_cluster_generations()` was called once.
- GREEN: the three focused stale/requeue regressions returned `3 passed`; `pytest -q` completed the full suite with exit code `0`.
- Scope note: the pre-existing `ensure_cluster_generations()` lock order remains unchanged for Task 2.
