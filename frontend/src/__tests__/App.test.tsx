import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";

const project = {
  id: "project-demo",
  name: "夏日家居上新",
  platform: "shopee",
  market: "SG",
  template: "商品基础套图",
  size: "1:1",
  status: "running",
  updatedAt: "2026-07-29T00:00:00Z",
  assets: [{ id: "asset-lamp-main", name: "desk-lamp-main.png", kind: "image", imageUrl: "/api/assets/asset-lamp-main/media/" }],
  skus: [{
    id: "sku-lamp",
    name: "桌面护眼灯",
    version: 1,
    assetIds: ["asset-lamp-main"],
    facts: "可调节灯臂",
    identityLock: "深蓝色灯头",
    brief: "极简书桌场景",
    outputs: [{ id: "generation-lamp-main", name: "白底主图", slot: "主图", attempt: 1, version: 1, status: "completed", reviewStatus: "pending", imageUrl: "/api/results/result-lamp-main/media/" }],
  }],
};

function response(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function renderApp(path = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubEnv("VITE_DEMO_MODE", "false");
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(
    response(200, url.includes("/workspace/") ? { projects: [project] } : project),
  )));
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("shows the operator dashboard from the Django workspace snapshot", async () => {
  renderApp();

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeInTheDocument();
  expect(await screen.findByText("夏日家居上新")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "新建出图项目" })).toBeInTheDocument();
});

test("marks the upload chooser as a native folder picker", async () => {
  renderApp("/projects/project-demo");

  const input = await screen.findByLabelText("选择图片或文件夹");
  expect(input).toHaveAttribute("webkitdirectory");
});

test("shows a product brief and output slots in the studio", async () => {
  renderApp("/projects/project-demo/studio/sku-lamp");

  expect(await screen.findByRole("heading", { name: "商品创作台" })).toBeInTheDocument();
  expect(screen.getByLabelText("商品卖点与规格")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /主图/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存 Brief" })).toBeInTheDocument();
});

test("requests the delivery ZIP and releases the temporary browser URL", async () => {
  const createObjectURL = vi.fn(() => "blob:delivery");
  const revokeObjectURL = vi.fn();
  const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
  const fetchMock = vi.fn((url: string, _init?: RequestInit) => Promise.resolve(
    url.includes("/workspace/")
      ? response(200, { projects: [project] })
      : url.includes("/export/")
        ? { ok: true, status: 200, blob: async () => new Blob(["zip"]) }
        : response(200, project),
  ));
  vi.stubGlobal("fetch", fetchMock);
  renderApp("/projects/project-demo");

  fireEvent.click(await screen.findByRole("button", { name: "导出可交付 ZIP" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/projects/project-demo/export/",
    { credentials: "same-origin" },
  ));
  expect(createObjectURL).toHaveBeenCalledTimes(1);
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:delivery");
  anchorClick.mockRestore();
});

test("shows an actionable error rather than mock content when workspace access is denied", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(403, { error: "forbidden" })));
  renderApp("/");

  expect(await screen.findByRole("alert")).toHaveTextContent("访问被拒绝");
});

test("submits a letterbox-normalized annotation when an operator requests changes", async () => {
  const fetchMock = vi.fn((url: string, _init?: RequestInit) => Promise.resolve(
    url.includes("/workspace/")
      ? response(200, { projects: [project] })
      : url.includes("/csrf/")
        ? response(200, { csrf_token: "csrf-for-test" })
        : response(200, { generation: { id: "generation-lamp-main" } }),
  ));
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 400, height: 400, top: 0, right: 400, bottom: 400, left: 0, x: 0, y: 0,
    toJSON: () => ({}),
  });
  renderApp("/review");

  expect(await screen.findByRole("heading", { name: "审核中心" })).toBeInTheDocument();
  const image = await screen.findByAltText("待审核结果");
  Object.defineProperty(image, "naturalWidth", { configurable: true, value: 800 });
  Object.defineProperty(image, "naturalHeight", { configurable: true, value: 400 });
  fireEvent.click(screen.getByLabelText("圈选图片问题"), { clientX: 200, clientY: 200 });
  fireEvent.click(screen.getByRole("button", { name: "请求修改" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/review/"))).toBe(true));
  const reviewCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/review/"));
  expect(JSON.parse(String(reviewCall?.[1]?.body))).toMatchObject({
    decision: "changes_requested",
    annotations: [{ kind: "circle", rect: [0.5, 0.5, 0.08, 0.08] }],
  });
});
