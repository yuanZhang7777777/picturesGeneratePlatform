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
