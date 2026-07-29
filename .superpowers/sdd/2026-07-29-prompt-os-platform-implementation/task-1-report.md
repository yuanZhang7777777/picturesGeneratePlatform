# Task 1 - Prompt OS platform implementation

## Scope

- Extended `Batch` with free-form `market`, selected output template/rule profile, and selected size/resolution.
- Added immutable generation-side template and rule snapshots.
- Added structured `PromptVersion` metadata and prompt-node / Style DNA models.
- Added the slot compiler, consumer-to-model-persona selection, and selected-template generation flow.

## TDD evidence

1. Added `tests/test_prompt_os.py` before production changes.
2. Red: `./.venv/Scripts/python.exe -m pytest tests/test_prompt_os.py -q` failed with missing `OutputTemplate.version`, `CompetitorInsight`, `compile_slot_prompt`, and `PromptNodeTemplate`.
3. Green: the same command passed with `4 passed`.
4. Migration check: `USE_SQLITE_FOR_TESTS=1 ./.venv/Scripts/python.exe manage.py makemigrations --check --dry-run` returned `No changes detected`.
5. Required regression set: `./.venv/Scripts/python.exe -m pytest tests/test_prompt_os.py tests/test_models.py tests/test_generation_queue.py -q` returned `14 passed`.
6. Full suite: `./.venv/Scripts/python.exe -m pytest -q` returned `39 passed`.

## Files changed

- `platform_app/models.py`
- `platform_app/services.py`
- `platform_app/admin.py`
- `platform_app/migrations/0002_prompt_os.py`
- `tests/test_prompt_os.py`

## Risks and follow-up

- The compiler accepts only an allowlist of Style DNA keys, but it does not perform semantic trademark detection inside otherwise-allowed text values; Style DNA extraction/review belongs in a later review workflow.
- Prompt-node publication uses transactional status switching. The current Django admin exposes the records; an explicit admin action/UI can be added later.
- This task creates snapshots at generation confirmation. APIs and UI for editing the new project configuration are intentionally left to the next assigned tasks.
