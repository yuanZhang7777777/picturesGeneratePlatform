import { afterEach, expect, test, vi } from "vitest";

import { createProject } from "../api";

afterEach(() => vi.unstubAllGlobals());

test("obtains a CSRF token from the same-origin bootstrap endpoint before creating a project", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ csrf_token: "csrf-for-test" }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "project-1", name: "新品" }) });
  vi.stubGlobal("fetch", fetchMock);

  await createProject({ name: "新品", platform: "Shopee", market: "SG", template: "商品基础套图", size: "1:1 · 1K" });

  expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/csrf/", { credentials: "same-origin" });
  expect(fetchMock.mock.calls[1][1].headers.get("X-CSRFToken")).toBe("csrf-for-test");
});
