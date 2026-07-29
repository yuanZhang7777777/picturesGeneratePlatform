import { afterEach, expect, test, vi } from "vitest";

import { createProject, loadWorkspace, mergeAsset, submitReview, uploadAssets } from "../api";

afterEach(() => vi.unstubAllGlobals());

const project = {
  id: "project-1",
  name: "新品",
  platform: "shopee",
  market: "SG",
  template: "商品基础套图",
  size: "1:1",
  status: "draft",
  updatedAt: "2026-07-29T00:00:00Z",
  assets: [],
  skus: [],
};

function response(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

test("obtains a CSRF token from the same-origin bootstrap endpoint before creating a project", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ csrf_token: "csrf-for-test" }) })
    .mockResolvedValueOnce(response(201, project));
  vi.stubGlobal("fetch", fetchMock);

  await createProject({ name: "新品", platform: "Shopee", market: "SG", template: "商品基础套图", size: "1:1 · 1K" });

  expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/csrf/", { credentials: "same-origin" });
  expect(fetchMock.mock.calls[1][1].headers.get("X-CSRFToken")).toBe("csrf-for-test");
});

test("canonicalizes market codes to uppercase without restricting unlisted countries", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ csrf_token: "csrf-for-test" }) })
    .mockResolvedValueOnce(response(201, project));
  vi.stubGlobal("fetch", fetchMock);

  await createProject({ name: "Brazil launch", platform: "shopee", market: "br", template: "", size: "1:1" });

  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ market: "BR" });
});

test("does not turn a 403 workspace response into mock data outside explicit demo mode", async () => {
  vi.stubEnv("VITE_DEMO_MODE", "false");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(403, { error: "forbidden" })));

  await expect(loadWorkspace()).rejects.toMatchObject({ status: 403, message: "forbidden" });
});

test("treats a redirected HTML login page as an authentication error instead of JSON", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    redirected: true,
    headers: new Headers({ "content-type": "text/html; charset=utf-8" }),
    json: async () => "<html>login</html>",
  }));

  await expect(loadWorkspace()).rejects.toMatchObject({
    status: 401,
    message: "登录已失效或需修改密码",
  });
});

test("keeps the relative folder path when posting a selected upload", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response(200, { csrf_token: "csrf-for-test" }))
    .mockResolvedValueOnce(response(200, { asset_count: 1 }));
  vi.stubGlobal("fetch", fetchMock);
  const file = new File(["image"], "front.png", { type: "image/png" });
  Object.defineProperty(file, "webkitRelativePath", { value: "lamp/angles/front.png" });

  await uploadAssets("project-1", [file]);

  const form = fetchMock.mock.calls[1][1].body as FormData;
  expect((form.get("files") as File).name).toBe("lamp/angles/front.png");
});

test("merges an asset through the versioned Django cluster endpoint", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response(200, { csrf_token: "csrf-for-test" }))
    .mockResolvedValueOnce(response(200, { id: "sku-target", version: 4 }));
  vi.stubGlobal("fetch", fetchMock);

  await mergeAsset("sku-target", "asset-side-angle", 3);

  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "/api/clusters/sku-target/merge/",
    expect.objectContaining({ credentials: "same-origin" }),
  );
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ asset_id: "asset-side-angle", expected_version: 3 });
});

test("submits normalized review annotations with the chosen decision", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response(200, { csrf_token: "csrf-for-test" }))
    .mockResolvedValueOnce(response(200, { generation: { id: "generation-1", attempt: 2 } }));
  vi.stubGlobal("fetch", fetchMock);

  await submitReview("generation-1", {
    decision: "changes_requested",
    issue_tags: ["logo"],
    description: "Logo needs correction",
    annotations: [{ kind: "circle", rect: [0.1, 0.2, 0.3, 0.4], color: "#ff0000", width: 2 }],
  });

  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
    decision: "changes_requested",
    issue_tags: ["logo"],
    description: "Logo needs correction",
    annotations: [{ kind: "circle", rect: [0.1, 0.2, 0.3, 0.4], color: "#ff0000", width: 2 }],
  });
});

test("submits an acceptance decision to the same real review endpoint", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response(200, { csrf_token: "csrf-for-test" }))
    .mockResolvedValueOnce(response(200, { generation: { id: "generation-1", attempt: 1 } }));
  vi.stubGlobal("fetch", fetchMock);

  await submitReview("generation-1", { decision: "accept", issue_tags: [], description: "", annotations: [] });

  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ decision: "accept" });
});

test("keeps a review failure visible to the caller instead of manufacturing success", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response(200, { csrf_token: "csrf-for-test" }))
    .mockResolvedValueOnce(response(500, { error: "review unavailable" }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(submitReview("generation-1", { decision: "accept", issue_tags: [], description: "", annotations: [] }))
    .rejects.toMatchObject({ status: 500, message: "review unavailable" });
});
