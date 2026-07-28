# Runbook

## Local Development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
$env:USE_SQLITE_FOR_TESTS='1'
$env:APIMART_FAKE_MODE='1'
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py seed_admin --username admin --password <local-password>
.\.venv\Scripts\python manage.py runserver 127.0.0.1:8000
```

## Docker Preview

Create `.env` from `.env.example`. Do not put `.env` into Git.

Required production values:

- `DJANGO_SECRET_KEY`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `DJANGO_ALLOWED_HOSTS`
- `ADMIN_PASSWORD`
- `APIMART_API_KEY` only after key rotation

Start:

```bash
docker compose up -d --build
docker compose exec -T web python manage.py seed_admin --username admin
curl -fsS http://127.0.0.1:18083/health/ready
```

## Hermes Preview Deployment

Hermes operations must use `ssh hermes-remote` and must not hard-code the server IP in scripts or docs.

Deployment is a global write operation:

```text
project_key=global
mode=deploy
target=/opt/independent-image-platform
locks=.codex_locks/global.lock
global_touch=yes
bridge_needed=no
```

Use `.codex_locks/global.lock` before creating or updating `/opt/independent-image-platform`.

## Security Notes

- Rotate any key pasted into chat before real use.
- The current preview is HTTP on port `18083`; use only test accounts and non-sensitive materials until domain HTTPS is configured.
- Keep `APIMART_FAKE_MODE=1` unless a paid smoke test is explicitly authorized.
- Store OSS credentials only in server `.env` after rotation; the MVP works with local private media storage.
