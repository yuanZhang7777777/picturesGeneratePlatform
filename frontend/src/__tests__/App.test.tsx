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
    outputs: [{ id: "generation-lamp-main", name: "白底主图", slot: "主图", slotId: "main", slotOrder: 1, attempt: 1, version: 1, status: "completed", reviewStatus: "pending", imageUrl: "/api/results/result-lamp-main/media/" }],
  }],
};

const reviewProject = {
  ...project,
  skus: [
    project.skus[0],
    {
      ...project.skus[0],
      id: "sku-second",
      name: "第二个商品",
      outputs: [{
        ...project.skus[0].outputs[0],
        id: "generation-second",
        name: "第二张待审核图",
      }],
    },
  ],
};

const productionProject = {
  ...project,
  skus: [{
    ...project.skus[0],
    outputs: [
      { ...project.skus[0].outputs[0], id: "generation-main-running", slot: "商品图", slotId: "main", slotOrder: 1, status: "running" },
      { ...project.skus[0].outputs[0], id: "generation-detail-old", slot: "商品图", slotId: "detail", slotOrder: 2, attempt: 1, status: "completed" },
      { ...project.skus[0].outputs[0], id: "generation-detail-failed", slot: "商品图", slotId: "detail", slotOrder: 2, attempt: 2, status: "failed", failureReason: "provider timeout" },
    ],
  }],
};

const studioHistoryProject = {
  ...project,
  skus: [{
    ...project.skus[0],
    outputs: [
      { ...project.skus[0].outputs[0], id: "generation-detail-old", slot: "详情图", slotId: "detail", slotOrder: 2, attempt: 1 },
      { ...project.skus[0].outputs[0], id: "generation-main", slot: "主图", slotId: "main", slotOrder: 1, attempt: 1 },
      { ...project.skus[0].outputs[0], id: "generation-detail-current", slot: "详情图", slotId: "detail", slotOrder: 2, attempt: 2 },
    ],
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

test.each(["US", "BR"])("accepts the global market code %s when creating a project", async (market) => {
  const fetchMock = vi.fn((url: string, _init?: RequestInit) => Promise.resolve(
    url.includes("/csrf/")
      ? response(200, { csrf_token: "csrf-for-test" })
      : response(201, { ...project, id: `project-${market}`, market }),
  ));
  vi.stubGlobal("fetch", fetchMock);
  renderApp("/projects/new");

  const marketInput = await screen.findByLabelText("市场");
  fireEvent.change(marketInput, { target: { value: market.toLowerCase() } });
  expect(marketInput).toHaveValue(market);
  expect(screen.getByText("通用基线")).toBeInTheDocument();
  expect(screen.getByText("市场专属规则待确认")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "创建项目并上传素材" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/projects/"))).toBe(true));
  const createCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/projects/");
  expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({ market });
});

test("describes an empty template as the published generic baseline", async () => {
  renderApp("/projects/new");

  expect(await screen.findByPlaceholderText("留空时使用已发布的通用基线模板")).toBeInTheDocument();
});

test("shows a product brief and output slots in the studio", async () => {
  renderApp("/projects/project-demo/studio/sku-lamp");

  expect(await screen.findByRole("heading", { name: "商品创作台" })).toBeInTheDocument();
  expect(screen.getByLabelText("商品卖点与规格")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /主图/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存 Brief" })).toBeInTheDocument();
});

test("shows only current server-ordered output slots in the studio", async () => {
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(
    response(200, url.includes("/projects/project-demo/snapshot/") ? studioHistoryProject : { projects: [studioHistoryProject] }),
  )));
  renderApp("/projects/project-demo/studio/sku-lamp");

  expect(await screen.findByRole("button", { name: "主图 第1位 v1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "详情图 第2位 v2" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "详情图 第2位 v1" })).not.toBeInTheDocument();
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

test("offers a login link when Django redirects an expired session to HTML", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    redirected: true,
    headers: new Headers({ "content-type": "text/html" }),
    json: async () => "<html>login</html>",
  }));
  renderApp("/");

  expect(await screen.findByRole("alert")).toHaveTextContent("登录已失效或需修改密码");
  expect(screen.getByRole("link", { name: "前往登录" })).toHaveAttribute("href", "/login/");
});

test("requires a tag or description before requesting image changes", async () => {
  renderApp("/review");

  expect(await screen.findByRole("button", { name: "请求修改" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "商品身份" })).toBeInTheDocument();
});

test("runs preflight before enabling an explicit generation confirmation", async () => {
  const fetchMock = vi.fn((url: string, _init?: RequestInit) => Promise.resolve(
    url.includes("/projects/project-demo/snapshot/")
      ? response(200, project)
      : url.includes("/csrf/")
        ? response(200, { csrf_token: "csrf-for-test" })
      : url.includes("/preflight/")
        ? response(200, { cluster_count: 1, slot_count: 2, generation_count: 2, blocking_errors: [] })
        : response(200, project),
  ));
  vi.stubGlobal("fetch", fetchMock);
  renderApp("/projects/project-demo/studio/sku-lamp");

  const confirm = await screen.findByRole("button", { name: "确认批量生成" });
  expect(confirm).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "运行预检" }));

  expect(await screen.findByText("将生成 2 张输出图")).toBeInTheDocument();
  expect(confirm).toBeEnabled();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/projects/project-demo/preflight/",
    expect.objectContaining({ method: "POST" }),
  );
});

test("confirms generation only after a passing preflight", async () => {
  const fetchMock = vi.fn((url: string) => Promise.resolve(
    url.includes("/projects/project-demo/snapshot/")
      ? response(200, project)
      : url.includes("/preflight/")
        ? response(200, { cluster_count: 1, slot_count: 2, generation_count: 2, blocking_errors: [] })
        : url.includes("/csrf/")
          ? response(200, { csrf_token: "csrf-for-test" })
          : response(200, project),
  ));
  vi.stubGlobal("fetch", fetchMock);
  renderApp("/projects/project-demo/studio/sku-lamp");

  fireEvent.click(await screen.findByRole("button", { name: "运行预检" }));
  const confirm = await screen.findByRole("button", { name: "确认批量生成" });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/confirm/"))).toBe(true));
  expect(fetchMock.mock.calls.findIndex(([url]) => String(url).includes("/api/clusters/sku-lamp/")))
    .toBeLessThan(fetchMock.mock.calls.findIndex(([url]) => String(url).includes("/confirm/")));
});

test("shows every active or failed output slot with its own retry action", async () => {
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(
    response(200, url.includes("/workspace/") ? { projects: [productionProject] } : url.includes("/csrf/") ? { csrf_token: "csrf-for-test" } : productionProject),
  )));
  renderApp("/production");

  expect(await screen.findByText("商品图 · 生成中")).toBeInTheDocument();
  expect(screen.getByText("商品图 · 需处理")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "只重做 商品图（第2位）" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    "/api/generations/generation-detail-failed/retry/",
    expect.objectContaining({ credentials: "same-origin" }),
  ));
});

test("keeps generation confirmation disabled when preflight reports blockers", async () => {
  const fetchMock = vi.fn((url: string) => Promise.resolve(
    url.includes("/projects/project-demo/snapshot/")
      ? response(200, project)
      : url.includes("/csrf/")
        ? response(200, { csrf_token: "csrf-for-test" })
      : url.includes("/preflight/")
        ? response(200, { cluster_count: 0, slot_count: 2, generation_count: 0, blocking_errors: ["batch has no image clusters"] })
        : response(200, project),
  ));
  vi.stubGlobal("fetch", fetchMock);
  renderApp("/projects/project-demo/studio/sku-lamp");

  const confirm = await screen.findByRole("button", { name: "确认批量生成" });
  fireEvent.click(screen.getByRole("button", { name: "运行预检" }));

  expect(await screen.findByText("batch has no image clusters")).toBeInTheDocument();
  expect(confirm).toBeDisabled();
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
  fireEvent.click(screen.getByRole("checkbox", { name: "商品身份" }));
  fireEvent.click(screen.getByRole("button", { name: "在结果图上添加问题圈选" }), { clientX: 0, clientY: 100 });
  fireEvent.click(screen.getByRole("button", { name: "请求修改" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/review/"))).toBe(true));
  const reviewCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/review/"));
  expect(JSON.parse(String(reviewCall?.[1]?.body))).toMatchObject({
    decision: "changes_requested",
    issue_tags: ["identity"],
    annotations: [{ kind: "circle", rect: [0, 0, 0.16, 0.16] }],
  });
});

test("places a horizontal-letterbox review marker inside the rendered image box", async () => {
  renderApp("/review");

  const canvas = await screen.findByRole("button", { name: "在结果图上添加问题圈选" });
  Object.defineProperty(canvas, "getBoundingClientRect", { configurable: true, value: () => ({ width: 400, height: 400, top: 0, right: 400, bottom: 400, left: 0, x: 0, y: 0, toJSON: () => ({}) }) });
  const image = await screen.findByAltText("待审核结果");
  Object.defineProperty(image, "naturalWidth", { configurable: true, value: 800 });
  Object.defineProperty(image, "naturalHeight", { configurable: true, value: 400 });
  fireEvent.click(canvas, { clientX: 100, clientY: 150 });

  const marker = screen.getByText("1");
  expect(marker).toHaveStyle({ left: "100px", top: "150px" });
});

test("places a vertical-letterbox review marker inside the rendered image box", async () => {
  renderApp("/review");

  const canvas = await screen.findByRole("button", { name: "在结果图上添加问题圈选" });
  Object.defineProperty(canvas, "getBoundingClientRect", { configurable: true, value: () => ({ width: 400, height: 400, top: 0, right: 400, bottom: 400, left: 0, x: 0, y: 0, toJSON: () => ({}) }) });
  const image = await screen.findByAltText("待审核结果");
  Object.defineProperty(image, "naturalWidth", { configurable: true, value: 400 });
  Object.defineProperty(image, "naturalHeight", { configurable: true, value: 800 });
  fireEvent.click(canvas, { clientX: 150, clientY: 100 });

  const marker = screen.getByText("1");
  expect(marker).toHaveStyle({ left: "150px", top: "100px" });
});

test("adds a centered annotation from keyboard input", async () => {
  renderApp("/review");

  const canvas = await screen.findByRole("button", { name: "在结果图上添加问题圈选" });
  fireEvent.keyDown(canvas, { key: "Enter" });

  expect(screen.getByText("1")).toBeInTheDocument();
});

test("retries the original review request after the operator changes selection", async () => {
  const reviewCalls: string[] = [];
  const fetchMock = vi.fn((url: string, _init?: RequestInit) => {
    if (url.includes("/workspace/")) return Promise.resolve(response(200, { projects: [reviewProject] }));
    if (url.includes("/csrf/")) return Promise.resolve(response(200, { csrf_token: "csrf-for-test" }));
    if (url.includes("/review/")) {
      reviewCalls.push(url);
      return Promise.resolve(response(reviewCalls.length === 1 ? 500 : 200, reviewCalls.length === 1 ? { error: "retry me" } : { generation: { id: "generation-lamp-main" } }));
    }
    return Promise.resolve(response(200, project));
  });
  vi.stubGlobal("fetch", fetchMock);
  renderApp("/review");

  fireEvent.click(await screen.findByRole("checkbox", { name: "商品身份" }));
  fireEvent.click(screen.getByRole("button", { name: "请求修改" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("retry me");
  fireEvent.click(screen.getByRole("button", { name: /第二个商品/ }));
  fireEvent.click(screen.getByRole("button", { name: "重试" }));

  await waitFor(() => expect(reviewCalls).toHaveLength(2));
  expect(reviewCalls).toEqual([
    "/api/generations/generation-lamp-main/review/",
    "/api/generations/generation-lamp-main/review/",
  ]);
});
