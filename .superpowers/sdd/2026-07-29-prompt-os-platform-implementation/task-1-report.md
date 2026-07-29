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

## Fix round 1

- Replaced permissive Style DNA pass-through with a value allowlist for composition, lighting, color, scene density, and camera; competitor subject, brand, copy, person, product, and material values are discarded.
- Preserved `prompt_override` as additional creative requirements while retaining product facts and identity lock.
- Renamed `PromptVersion.model` to `provider_model` so model persona remains a prompt-only strategy rather than a claim of provider-model switching.
- Blocked confirmation against draft or retired templates/rules, made the fallback template explicitly published, and made used prompt snapshots read-only in both model saves and Django admin.
- Prefetched cluster references and competitor insights, resolved the published prompt node once per confirmation, and added a SELECT-count regression test.
- Strengthened snapshot coverage by mutating the source template, slot, and rule after confirmation and asserting the generation record remains unchanged.

### Fix round 1 verification

1. Red: `./.venv/Scripts/python.exe -m pytest tests/test_prompt_os.py -q` returned 10 failures for the new review cases.
2. Green: the same command returned `11 passed`.
3. Required regression set: `./.venv/Scripts/python.exe -m pytest tests/test_prompt_os.py tests/test_models.py tests/test_generation_queue.py -q` returned `21 passed`.
4. Full suite: `./.venv/Scripts/python.exe -m pytest -q` returned `46 passed`.
5. SQLite migration check: `USE_SQLITE_FOR_TESTS=1 ./.venv/Scripts/python.exe manage.py makemigrations --check --dry-run` returned `No changes detected`.

### Remaining risk

- The Style DNA contract is intentionally narrow. New visual descriptors need an explicit allowlist update and regression test; arbitrary prose is rejected rather than guessed.

## Regression found during full verification

- Full-suite execution exposed an intermittent failure in the existing idempotent-confirmation test: newly created generations were returned in cluster-loop order, while repeat confirmation returned `(created_at, id)` order. UUID tie-breaking made the two lists diverge when timestamps matched.
- `confirm_generation()` now returns the same canonical `(created_at, id)` ordering on both initial and repeated confirmation. The focused idempotency test passed in 10 consecutive fresh processes after the change.
