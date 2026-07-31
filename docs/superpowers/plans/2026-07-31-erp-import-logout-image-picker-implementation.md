# ERP Import, Logout and Image Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ERP SKU images import successfully, expose a safe logout action, and make single/multiple image selection obvious.

**Architecture:** Keep the existing ERP session-token and upload pipelines. Add only a small React logout client/action, clearer native file-picker controls, and the confirmed ERP image-source IP in the protected server environment.

**Tech Stack:** Django 5.2, React, TypeScript, Vitest, Docker Compose.

**Status:** Code and protected configuration deployed as Git `27a580f` on 2026-07-31. Production health, UI bundle labels, logout session clearing and real ERP JPEG download passed; employee-browser SKU preview and OSS verification remain open.

## Global Constraints

- Never expose or persist the ERP Token in browser storage.
- Logout must use POST with Django CSRF protection.
- `CATALOG_ALLOWED_IMAGE_HOSTS` must contain only confirmed public IP literals; never permit arbitrary hosts.
- Both image and folder selection reuse the existing upload path and limits.
- Do not touch `docs/project/用户操作流程以及相关触发.md`.

---

### Task 1: Logout and explicit image selection

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/layout.tsx`
- Modify: `frontend/src/components/ImportPanel.tsx`
- Test: `frontend/src/__tests__/App.test.tsx`
- Test: `tests/test_auth_permissions.py`

**Interfaces:**
- Consumes: `GET /api/csrf/`, existing `POST /logout/`, `ImportPanel.onUpload`.
- Produces: `logoutUser(): Promise<void>` and visible “选择单张 / 多张图片” button.

- [x] **Step 1: Write failing tests**

```tsx
expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
expect(screen.getByRole("button", { name: "选择单张 / 多张图片" })).toBeInTheDocument();
expect(screen.getByLabelText("选择单张 / 多张图片")).toHaveAttribute("multiple");
```

```python
def test_logout_clears_erp_token(client):
    user = make_user("logout-user")
    client.force_login(user)
    session = client.session
    session["erp_access_token"] = "token"
    session.save()
    response = client.post(reverse("logout"))
    assert response.status_code == 302
    assert "erp_access_token" not in client.session
```

- [x] **Step 2: Run focused tests and verify they fail for the missing controls/behavior**

Run:

```powershell
npm --prefix frontend test -- --run src/__tests__/App.test.tsx
E:\Project\picturesGenerate\.venv\Scripts\python.exe -m pytest -o addopts='' -q tests/test_auth_permissions.py
```

- [x] **Step 3: Implement the minimum UI and API changes**

Add `logoutUser()` using the existing CSRF helper and a same-origin POST. Add the header button and redirect to `/login/` only after success. Rename the visible image picker and its accessible label; keep `multiple` and keep the folder input separate.

- [x] **Step 4: Run the focused tests and verify they pass**

Use the commands from Step 2; expected result is zero failures.

### Task 2: Protected ERP image-source configuration and release

**Files:**
- Modify: `.env.example`
- Modify: `docs/runbook.md`
- Modify: `docs/project/STATUS.md`
- Server config: `/opt/independent-image-platform/.env`

**Interfaces:**
- Consumes: existing `CATALOG_ALLOWED_IMAGE_HOSTS` setting and `download_catalog_image`.
- Produces: exact allowlist entry `180.167.156.35`.

- [x] **Step 1: Add the confirmed ERP image IP to the example and runbook**

Set:

```text
CATALOG_ALLOWED_IMAGE_HOSTS=180.167.156.35
```

- [x] **Step 2: Run complete verification**

```powershell
E:\Project\picturesGenerate\.venv\Scripts\python.exe -m pytest -o addopts='' -q
npm --prefix frontend test
npm --prefix frontend run build
E:\Project\picturesGenerate\.venv\Scripts\python.exe manage.py check
E:\Project\picturesGenerate\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
git diff --check
```

- [x] **Step 3: Deploy under the Hermes global lock**

Back up the current release and `.env`, update only `CATALOG_ALLOWED_IMAGE_HOSTS`, deploy the verified commit, and recreate affected containers.

- [ ] **Step 4: Run production smoke**

Verify `/health/ready`, confirm the container receives the allowlist, log in with an ERP employee account, import one confirmed SKU, verify its image preview and OSS object, then verify “退出登录” returns to `/login/`.
