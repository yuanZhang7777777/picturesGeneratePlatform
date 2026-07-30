import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";

const slotNames = [
  "白底标准图",
  "第二角度/结构图",
  "核心卖点图",
  "材质或细节图",
  "使用场景图",
  "模特或比例展示图",
  "尺寸/包装/包含物图",
  "平台转化营销图",
  "补充转化图",
];

const outputs = slotNames.flatMap((slot, index) => {
  const order = index + 1;
  const base = {
    id: `generation-${order}`,
    name: slot,
    slot,
    slotId: `slot-${order}`,
    slotOrder: order,
    attempt: 1,
    version: 1,
    status: "completed",
    reviewStatus: "pending",
    imageUrl: `/api/results/result-${order}/media/`,
    prompt: `${slot} prompt`,
  };
  return order === 2
    ? [{ ...base, id: "generation-2-old", attempt: 1 }, { ...base, id: "generation-2", attempt: 2, version: 2 }]
    : [base];
});

const project = {
  id: "project-demo",
  name: "夏日家居上新",
  platform: "Shopee",
  market: "SG",
  template: "商品基础套图",
  size: "1:1",
  resolution: "1k",
  status: "running",
  updatedAt: "2026-07-29T00:00:00Z",
  assets: [
    { id: "asset-lamp-main", name: "desk-lamp-main.png", kind: "image", imageUrl: "/api/assets/asset-lamp-main/media/" },
    { id: "asset-lamp-side", name: "desk-lamp-side.png", kind: "image", imageUrl: "/api/assets/asset-lamp-side/media/" },
  ],
  skus: [{
    id: "sku-lamp",
    name: "桌面护眼灯",
    sku: "LAMP-001",
    relationType: "single_product",
    version: 1,
    assetIds: ["asset-lamp-main"],
    assets: [{ id: "asset-lamp-main", name: "desk-lamp-main.png", kind: "image", imageUrl: "/api/assets/asset-lamp-main/media/" }],
    facts: "可调节灯臂",
    identityLock: "深蓝色灯头",
    brief: "极简书桌场景",
    preparationStatus: "ready",
    prompts: slotNames.map((slot, index) => ({ slotOrder: index + 1, slot, text: `${slot} prompt` })),
    outputs,
  }],
};

function response(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function renderApp(path = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function stubFetch(handler?: (url: string, init?: RequestInit) => Promise<unknown>) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (handler) return handler(url, init);
    if (url.includes("/csrf/")) return Promise.resolve(response(200, { csrf_token: "csrf-for-test" }));
    if (url.includes("/workspace/")) return Promise.resolve(response(200, { projects: [project] }));
    return Promise.resolve(response(200, project));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.stubEnv("VITE_DEMO_MODE", "false");
  stubFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("shows the operator dashboard without the old review center", async () => {
  renderApp();

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeInTheDocument();
  expect(screen.getByText("夏日家居上新")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "新建出图项目" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "审核中心" })).not.toBeInTheDocument();
});

test("opens a unified project workspace from the dashboard", async () => {
  renderApp("/projects/project-demo");

  expect(await screen.findByRole("heading", { name: "夏日家居上新" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "生产与结果" })).toHaveAttribute("href", "/projects/project-demo/results");
});

test("shows Shopee shop type and hides it for TikTok Shop", async () => {
  renderApp("/projects/new");

  expect(await screen.findByLabelText("店铺类型")).toHaveValue("general");
  fireEvent.change(screen.getByLabelText("平台"), { target: { value: "tiktok" } });
  expect(screen.queryByLabelText("店铺类型")).not.toBeInTheDocument();
});

test("shows two explicit import choices for both upload and ERP SKU entry", async () => {
  renderApp("/projects/project-demo");

  expect(await screen.findAllByRole("button", { name: "导入并自动出图" })).toHaveLength(2);
  expect(screen.getAllByRole("button", { name: "导入后整理" })).toHaveLength(2);
  expect(screen.getByLabelText("选择图片")).not.toHaveAttribute("webkitdirectory");
  expect(screen.getByLabelText("选择文件夹")).toHaveAttribute("webkitdirectory");
  expect(screen.getByText("拖入图片或文件夹")).toBeInTheDocument();
  expect(screen.getByLabelText("ERP SKU")).toBeInTheDocument();
});

test("posts uploaded files in automatic mode and immediately requests generation", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  const input = await screen.findByLabelText("选择图片");
  const file = new File(["image"], "front.png", { type: "image/png" });
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getAllByRole("button", { name: "导入并自动出图" })[0]);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/assets/"))).toBe(true));
  const uploadCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/assets/"));
  expect((uploadCall?.[1]?.body as FormData).get("mode")).toBe("auto");
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/generate/"))).toBe(true));
});

test("posts uploaded files in organize mode without starting generation", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  fireEvent.change(await screen.findByLabelText("选择图片"), {
    target: { files: [new File(["image"], "front.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getAllByRole("button", { name: "导入后整理" })[0]);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/assets/"))).toBe(true));
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/generate/"))).toBe(false);
});

test("imports ERP SKUs in organize mode", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  fireEvent.change(await screen.findByLabelText("ERP SKU"), { target: { value: "LAMP-001\nLAMP-002" } });
  fireEvent.click(screen.getAllByRole("button", { name: "导入后整理" })[1]);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/sku-import/"))).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/sku-import/"));
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ skus: ["LAMP-001", "LAMP-002"], mode: "organize" });
});

test("auto ERP import enters the same generation path as upload", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  fireEvent.change(await screen.findByLabelText("ERP SKU"), { target: { value: "LAMP-001" } });
  fireEvent.click(screen.getAllByRole("button", { name: "导入并自动出图" })[1]);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/sku-import/"))).toBe(true));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/generate/"))).toBe(true));
});

test("renders product cards with relation choice and prompt editing", async () => {
  renderApp("/projects/project-demo");

  expect(await screen.findByRole("heading", { name: "桌面护眼灯" })).toBeInTheDocument();
  expect(screen.getByDisplayValue("一图一商品")).toBeInTheDocument();
  expect(screen.getByLabelText("商品名称")).toHaveValue("桌面护眼灯");
  expect(screen.getByLabelText("身份锁")).toHaveValue("深蓝色灯头");
  expect(screen.getByLabelText("01 白底标准图 Prompt")).toHaveValue("白底标准图 prompt");
});

test("saves edited product relation and prompts through the cluster endpoint", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  fireEvent.change(await screen.findByLabelText("整套要求"), { target: { value: "更明亮的书桌场景" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Prompt" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/clusters/sku-lamp/"))).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/clusters/sku-lamp/"));
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ prompt_override: "更明亮的书桌场景" });
});

test("starts generation for selected products and shows product and image counts", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  const button = await screen.findByRole("button", { name: "生成选中商品（1 个商品 / 9 张图）" });
  fireEvent.click(button);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/generate/"))).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/generate/"));
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ cluster_ids: ["sku-lamp"], slot_orders: [1, 2, 3, 4, 5, 6, 7, 8, 9] });
});

test("offers merge and undo affordances for product grouping", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  fireEvent.click(await screen.findByRole("button", { name: "合并 desk-lamp-side.png 到桌面护眼灯" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/merge/"))).toBe(true));
  expect(screen.getByRole("button", { name: "撤销上次合并" })).toBeInTheDocument();
});

test("shows a nine-slot result grid for the project", async () => {
  renderApp("/projects/project-demo/results");

  expect(await screen.findByRole("heading", { name: "生产与结果" })).toBeInTheDocument();
  for (const slot of slotNames) expect(screen.getByText(slot)).toBeInTheDocument();
});

test("selects the latest successful version by default for export", async () => {
  renderApp("/projects/project-demo/results");

  const latest = await screen.findByRole("checkbox", { name: "导出 第二角度/结构图 v2" });
  expect(latest).toBeChecked();
  expect(screen.getByRole("button", { name: "历史版本 第二角度/结构图 v1" })).toBeInTheDocument();
});

test("lets operators cancel one result from the ZIP selection", async () => {
  renderApp("/projects/project-demo/results");

  const first = await screen.findByRole("checkbox", { name: "导出 白底标准图 v1" });
  fireEvent.click(first);

  expect(first).not.toBeChecked();
  expect(screen.getByRole("button", { name: "下载选中 ZIP（8 张）" })).toBeInTheDocument();
});

test("posts only selected generation IDs when downloading the ZIP", async () => {
  const fetchMock = stubFetch((url) => {
    if (url.includes("/csrf/")) return Promise.resolve(response(200, { csrf_token: "csrf-for-test" }));
    if (url.includes("/export/")) return Promise.resolve({ ok: true, status: 200, blob: async () => new Blob(["zip"]) });
    return Promise.resolve(response(200, url.includes("/workspace/") ? { projects: [project] } : project));
  });
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:zip"), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  renderApp("/projects/project-demo/results");

  fireEvent.click(await screen.findByRole("checkbox", { name: "导出 白底标准图 v1" }));
  fireEvent.click(screen.getByRole("button", { name: "下载选中 ZIP（8 张）" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/export/"))).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/export/"));
  expect(JSON.parse(String(call?.[1]?.body)).generation_ids).not.toContain("generation-1");
});

test("requests a new version for a successful result", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo/results");

  fireEvent.click(await screen.findByRole("button", { name: "再生成 白底标准图" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/generations/generation-1/regenerate/",
    expect.objectContaining({ method: "POST" }),
  ));
});

test("keeps revision submission disabled until a tag or description is present", async () => {
  renderApp("/projects/project-demo/results");

  expect(await screen.findByRole("button", { name: "提交圈选修改" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "商品身份" })).toBeInTheDocument();
});

test("submits a normalized annotation to the revision endpoint", async () => {
  const fetchMock = stubFetch();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 400, height: 400, top: 0, right: 400, bottom: 400, left: 0, x: 0, y: 0,
    toJSON: () => ({}),
  });
  renderApp("/projects/project-demo/results");

  const image = await screen.findByAltText("当前白底标准图 结果图");
  Object.defineProperty(image, "naturalWidth", { configurable: true, value: 800 });
  Object.defineProperty(image, "naturalHeight", { configurable: true, value: 400 });
  fireEvent.click(screen.getByRole("checkbox", { name: "商品身份" }));
  fireEvent.click(screen.getByRole("button", { name: "在结果图上添加问题圈选" }), { clientX: 0, clientY: 100 });
  fireEvent.click(screen.getByRole("button", { name: "提交圈选修改" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/revise/"))).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/revise/"));
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    issue_tags: ["identity"],
    annotations: [{ kind: "circle", rect: [0, 0, 0.16, 0.16] }],
  });
});

test("routes production links to the project result page", async () => {
  renderApp("/production");

  expect(await screen.findByRole("heading", { name: "生产队列" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看结果" })).toHaveAttribute("href", "/projects/project-demo/results");
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
