# Independent Image Platform MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deployable internal image-generation MVP for login, folder-style uploads, image clusters, prompts, async APIMart generation, failure-only retry, result history, and IP:port preview deployment.

**Architecture:** Use a Django 5.2 monolith with PostgreSQL, Django templates, vanilla JavaScript polling, and two management-command workers. Keep storage pluggable: local private disk works immediately; OSS is enabled only by server-side environment variables after credential rotation.

**Tech Stack:** Python 3.12, Django 5.2, PostgreSQL 16, Gunicorn, Docker Compose, Caddy, Pillow, requests, optional aliyun-oss2.

## Global Constraints

- Internal employees only; no public registration.
- Temporary preview uses IP + port `18083`; real employee passwords and real product materials require HTTPS before wider rollout.
- Do not use Feishu or Coze.
- Do not commit API keys, OSS keys, passwords, signed URLs, or prompt contents into logs.
- Default one uploaded image equals one product cluster; users can merge multiple images into one cluster.
- Retry only failed generations; keep old attempts and completed results.
- Organization daily image submission cap is `2000`; default user daily cap is `100`.
- Single batch image cap is `100`; single cluster reference cap is `16`; single confirmation generation cap is `300`.
- APIMart generation defaults are `model=gpt-image-2`, `n=1`, `size=1:1`, `resolution=1k`.
- Use local storage unless rotated OSS credentials are supplied in environment variables.
- Use Docker Compose project name `independent-image-platform`; do not touch services on ports `18081` or `18082`.
- Remote deployment to Hermes is a global write/deploy operation and must hold `.codex_locks/global.lock`.

---

## File Structure

- `pyproject.toml`: Python dependencies and pytest settings.
- `.gitignore`: Ignore virtualenvs, local DB, media, env files, caches.
- `.env.example`: Environment variable names only, with safe dummy values.
- `manage.py`: Django entry point.
- `image_platform/settings.py`: Django settings and environment parsing.
- `image_platform/urls.py`: Route table.
- `image_platform/wsgi.py`: Gunicorn entry point.
- `platform_app/models.py`: Domain model, quotas, states, attempts, audit events.
- `platform_app/services.py`: Storage, validation, APIMart client, preflight, queue processing.
- `platform_app/views.py`: HTML views and JSON endpoints.
- `platform_app/admin.py`: Admin registration.
- `platform_app/management/commands/*.py`: Worker, seed-admin, fake-load commands.
- `platform_app/templates/platform_app/*.html`: Minimal usable UI.
- `platform_app/static/platform_app/app.js`: Folder upload, cluster merge, polling, retry actions.
- `tests/*.py`: Focused tests for model behavior, quotas, permissions, generation retry, and API endpoints.
- `docker/Dockerfile`, `docker/entrypoint.sh`, `docker/Caddyfile`: Container runtime.
- `docker-compose.yml`: Local/remote deploy stack binding `18083`.
- `docs/runbook.md`: Run, deploy, configure, and verify.

## Task 1: Project Baseline

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `manage.py`
- Create: `image_platform/__init__.py`
- Create: `image_platform/settings.py`
- Create: `image_platform/urls.py`
- Create: `image_platform/wsgi.py`
- Create: `platform_app/__init__.py`
- Create: `platform_app/apps.py`
- Create: `tests/test_settings.py`

**Interfaces:**
- Produces: Django project named `image_platform`, app named `platform_app`.
- Produces: settings variables `APIMART_API_KEY`, `STORAGE_BACKEND`, `LOCAL_MEDIA_ROOT`, `MAX_ACTIVE_GENERATIONS`, `ORG_DAILY_GENERATION_LIMIT`.

- [ ] Write `tests/test_settings.py` asserting secrets are not required for fake mode and `.env.example` has no live secrets.
- [ ] Run `python -m pytest tests/test_settings.py -q`; expected failure because files do not exist.
- [ ] Create Django baseline files and safe environment parsing.
- [ ] Run `python -m pytest tests/test_settings.py -q`; expected pass.

## Task 2: Domain Model

**Files:**
- Create: `platform_app/models.py`
- Create: `platform_app/migrations/0001_initial.py` via `python manage.py makemigrations`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `Batch`, `Asset`, `Cluster`, `ClusterAsset`, `PromptVersion`, `Generation`, `ResultAsset`, `AuditEvent`, `RuleProfile`, `OutputTemplate`, `OutputSlot`.
- Produces: `Generation.retry_failed(user) -> Generation`.
- Produces: `Batch.recompute_status() -> str`.

- [ ] Write tests that one image creates one cluster, a cluster rejects more than 16 references, attempts are unique per cluster/slot, and retry only works from `failed`.
- [ ] Run `python -m pytest tests/test_models.py -q`; expected failure because models do not exist.
- [ ] Implement models with UUID primary keys, owner relations, state choices, quotas, and unique constraints.
- [ ] Run migrations and `python -m pytest tests/test_models.py -q`; expected pass.

## Task 3: Auth And Permissions

**Files:**
- Modify: `platform_app/models.py`
- Create: `platform_app/forms.py`
- Create: `platform_app/views.py`
- Modify: `image_platform/urls.py`
- Create: `tests/test_auth_permissions.py`

**Interfaces:**
- Produces: `require_owner_or_admin(user, obj) -> None`.
- Produces: login, logout, password-change, batch list routes.

- [ ] Write tests that anonymous users redirect to login, operators cannot read another user's batch, admins can, and first-login users must change password.
- [ ] Run `python -m pytest tests/test_auth_permissions.py -q`; expected failure because routes do not exist.
- [ ] Implement forms, views, decorators, session settings, and object-level checks.
- [ ] Run `python -m pytest tests/test_auth_permissions.py -q`; expected pass.

## Task 4: Upload And Cluster Workflow

**Files:**
- Modify: `platform_app/services.py`
- Modify: `platform_app/views.py`
- Create: `tests/test_upload_clusters.py`

**Interfaces:**
- Produces: `create_batch(owner, name, platform, site) -> Batch`.
- Produces: `register_uploaded_asset(batch, filename, content, content_type) -> Asset`.
- Produces: `merge_asset_into_cluster(asset, target_cluster) -> Cluster`.
- Produces: `move_asset_to_new_cluster(asset) -> Cluster`.

- [ ] Write tests for JPEG/PNG acceptance, TXT acceptance, invalid format rejection, default cluster per image, merge, split, and version conflict.
- [ ] Run `python -m pytest tests/test_upload_clusters.py -q`; expected failure because services are missing.
- [ ] Implement local private upload storage and image validation with Pillow.
- [ ] Implement cluster merge/split endpoints and optimistic version checks.
- [ ] Run `python -m pytest tests/test_upload_clusters.py -q`; expected pass.

## Task 5: Generation Queue

**Files:**
- Modify: `platform_app/services.py`
- Create: `platform_app/management/commands/run_generation_worker.py`
- Create: `tests/test_generation_queue.py`

**Interfaces:**
- Produces: `preflight_batch(batch, user) -> dict`.
- Produces: `confirm_generation(batch, user, slot_count=1) -> list[Generation]`.
- Produces: `process_generation_once(client, storage) -> int`.

- [ ] Write tests for organization daily cap `2000`, user cap `100`, batch cap `300`, idempotent confirmation, failed-only retry, result archival, and `submit_unknown`.
- [ ] Run `python -m pytest tests/test_generation_queue.py -q`; expected failure because queue functions do not exist.
- [ ] Implement quota accounting, queue state changes, fake APIMart client, and local result archival.
- [ ] Run `python -m pytest tests/test_generation_queue.py -q`; expected pass.

## Task 6: APIMart Client And Prompt Optimizer

**Files:**
- Modify: `platform_app/services.py`
- Create: `platform_app/management/commands/run_prompt_worker.py`
- Create: `tests/test_apimart_client.py`

**Interfaces:**
- Produces: `APIMartClient.submit_generation(prompt, image_paths, size, resolution) -> str`.
- Produces: `APIMartClient.get_task(task_id) -> dict`.
- Produces: `APIMartClient.optimize_prompt(payload) -> dict`.

- [ ] Write tests with a local fake HTTP responder for task submission, task polling, completed image URL flattening, 429 handling, and no-secret logging.
- [ ] Run `python -m pytest tests/test_apimart_client.py -q`; expected failure because client is missing.
- [ ] Implement `requests` client with bearer auth from environment, timeout, sanitized errors, and status normalization.
- [ ] Run `python -m pytest tests/test_apimart_client.py -q`; expected pass.

## Task 7: Usable UI

**Files:**
- Create: `platform_app/templates/platform_app/base.html`
- Create: `platform_app/templates/platform_app/login.html`
- Create: `platform_app/templates/platform_app/batch_list.html`
- Create: `platform_app/templates/platform_app/batch_detail.html`
- Create: `platform_app/static/platform_app/app.js`
- Create: `tests/test_views.py`

**Interfaces:**
- Produces: `/`, `/login/`, `/logout/`, `/password/change/`, `/batches/`, `/batches/new/`, `/batches/<uuid>/`, `/api/batches/<uuid>/snapshot/`, `/api/generations/<uuid>/retry/`.

- [ ] Write tests that pages render for logged-in users, JSON snapshots include batch/cluster/generation states, and retry endpoint creates a new attempt only for failed generations.
- [ ] Run `python -m pytest tests/test_views.py -q`; expected failure because templates/endpoints are missing.
- [ ] Implement restrained operational UI with folder file input, upload list, cluster cards, prompt fields, preflight, confirm, progress polling, review, and retry buttons.
- [ ] Run `python -m pytest tests/test_views.py -q`; expected pass.

## Task 8: Docker And Runbook

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker/entrypoint.sh`
- Create: `docker/Caddyfile`
- Create: `docker-compose.yml`
- Create: `docs/runbook.md`
- Modify: `README.md`

**Interfaces:**
- Produces: `docker compose up -d --build`.
- Produces: `python manage.py seed_admin --username admin --password <password>`.
- Produces: `/health/live` and `/health/ready`.

- [ ] Write tests for health endpoints and seed-admin idempotency.
- [ ] Run `python -m pytest tests/test_health_seed.py -q`; expected failure because endpoints/command are missing.
- [ ] Implement health views, seed-admin command, Docker files, compose stack, and runbook.
- [ ] Run `python -m pytest -q`; expected pass.
- [ ] Run `docker compose config`; expected exit `0`.

## Task 9: Hermes Preview Deployment

**Files:**
- No repository file required unless deployment evidence updates `docs/runbook.md`.

**Interfaces:**
- Consumes: Docker Compose stack from Task 8.
- Produces: Preview service at `http://<server>:18083` after lock-protected deployment.

- [ ] Declare Hermes operation with `project_key=global`, `mode=deploy`, `target=/opt/independent-image-platform`, `locks=.codex_locks/global.lock`, `global_touch=yes`, `bridge_needed=no`.
- [ ] Acquire non-blocking `global.lock`; stop if occupied.
- [ ] Copy source without `.env`, `.venv`, media files, caches, or secrets.
- [ ] Create remote `.env` from environment variable names only; use secret values supplied out-of-band or already present on server.
- [ ] Run `docker compose up -d --build`.
- [ ] Run `docker compose exec -T web python manage.py migrate`.
- [ ] Run `docker compose exec -T web python manage.py seed_admin --username admin`.
- [ ] Verify `curl -fsS http://127.0.0.1:18083/health/ready`.

## Self-Review

- Spec coverage: This plan implements internal login, object ownership, upload, default clusters, merge/split, prompts, preflight, quotas, async queue, APIMart client, failure-only retry, history, polling UI, Docker, runbook, and Hermes preview. Full Shopee/TikTok rule authoring, OSS production archival, HTTPS domain rollout, and 100-user/2,000-real-image provider ramp remain phase-two work after rotated credentials and provider contract tests.
- Placeholder scan: No `TBD`, `TODO`, `implement later`, or unspecified validation steps are used.
- Type consistency: Service names and route names are defined before consumers use them.
