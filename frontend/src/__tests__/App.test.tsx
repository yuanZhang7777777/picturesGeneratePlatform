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
  configurationStatus: "configured",
  defaultConfig: { platform: "shopee", market: "SG", sellerTier: "general", size: "1:1", resolution: "1k", globalPrompt: "" },
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

async function openImportPanel() {
  await screen.findByRole("region", { name: "添加商品面板" });
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
  expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "新建出图项目" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "审核中心" })).not.toBeInTheDocument();
});

test("redirects to login only after logout succeeds", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { assign, origin: "http://localhost:3000" });
  stubFetch(async (url) => {
    if (url.includes("/csrf/")) return response(200, { csrf_token: "csrf-for-test" });
    if (url === "/logout/") return { ok: true, status: 200, redirected: true, url: "http://localhost:3000/login/" };
    if (url.includes("/workspace/")) return response(200, { projects: [project] });
    return response(200, project);
  });
  renderApp();

  fireEvent.click(await screen.findByRole("button", { name: "退出登录" }));

  await waitFor(() => expect(assign).toHaveBeenCalledWith("/login/"));
});

test("shows a logout error and stays on the page when logout fails", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { assign, origin: "http://localhost:3000" });
  stubFetch(async (url) => {
    if (url.includes("/csrf/")) return response(200, { csrf_token: "csrf-for-test" });
    if (url === "/logout/") return response(500, { error: "logout unavailable" });
    if (url.includes("/workspace/")) return response(200, { projects: [project] });
    return response(200, project);
  });
  renderApp();

  fireEvent.click(await screen.findByRole("button", { name: "退出登录" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("退出登录失败，请重试。");
  expect(screen.getByRole("heading", { name: "工作台" })).toBeInTheDocument();
  expect(assign).not.toHaveBeenCalled();
});

test("opens a unified project workspace from the dashboard", async () => {
  renderApp("/projects/project-demo");

  expect(await screen.findByRole("heading", { name: "夏日家居上新" })).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "生产结果" })[0]).toHaveAttribute("href", "/projects/project-demo/results");
});

test("asks only for a project name before opening the project workbench", async () => {
  renderApp("/projects/new");

  expect(await screen.findByLabelText("项目名称")).toBeInTheDocument();
  expect(screen.queryByLabelText("平台")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("国家")).not.toBeInTheDocument();
});

test("keeps platform and country directly in the compact toolbar", async () => {
  renderApp("/projects/project-demo");

  expect(await screen.findByRole("button", { name: "Shopee 虾皮" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByLabelText("项目国家")).toHaveValue("SG");
  expect(screen.queryByLabelText("项目店铺类型")).not.toBeInTheDocument();
});

test("keeps the add-product panel permanently visible", async () => {
  renderApp("/projects/project-demo");

  expect(await screen.findByRole("region", { name: "添加商品面板" })).toBeInTheDocument();
  expect(screen.getByLabelText("ERP SKU")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "选择图片/文件夹" })).toBeInTheDocument();
});

test("does not hide the add-product panel behind a dialog", async () => {
  renderApp("/projects/project-demo");

  await openImportPanel();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.getByRole("region", { name: "添加商品面板" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "添加商品" })).not.toBeInTheDocument();
});

test("shows two explicit import choices for both upload and ERP SKU entry", async () => {
  renderApp("/projects/project-demo");

  await openImportPanel();
  fireEvent.click(screen.getByRole("button", { name: "选择图片/文件夹" }));
  expect(screen.getByRole("menu", { name: "添加素材方式" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "选择图片" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "选择文件夹" })).toBeInTheDocument();
  expect(screen.getByLabelText("选择图片")).toHaveAttribute("multiple");
  expect(screen.getByLabelText("选择图片")).not.toHaveAttribute("webkitdirectory");
  expect(screen.getByLabelText("选择文件夹")).toHaveAttribute("multiple");
  expect(screen.getByLabelText("选择文件夹")).toHaveAttribute("webkitdirectory");
  expect(screen.getByText("拖入图片或文件夹")).toBeInTheDocument();
  expect(screen.getByLabelText("ERP SKU")).toBeInTheDocument();
});

test("marks uploaded files for automatic mode without generating before Prompt preparation", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  await openImportPanel();
  fireEvent.click(screen.getByRole("button", { name: "选择图片/文件夹" }));
  const input = await screen.findByLabelText("选择图片");
  const file = new File(["image"], "front.png", { type: "image/png" });
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getAllByRole("button", { name: "导入并自动出图" })[0]);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/assets/"))).toBe(true));
  const uploadCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/assets/"));
  expect((uploadCall?.[1]?.body as FormData).get("mode")).toBe("auto");
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/generate/"))).toBe(false);
});

test("posts uploaded files in organize mode without starting generation", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  await openImportPanel();
  fireEvent.click(screen.getByRole("button", { name: "选择图片/文件夹" }));
  fireEvent.change(await screen.findByLabelText("选择图片"), {
    target: { files: [new File(["image"], "front.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getAllByRole("button", { name: "导入后整理" })[0]);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/assets/"))).toBe(true));
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/generate/"))).toBe(false);
});

test("previews pending images without filenames and does not resubmit successful files", async () => {
  const fetchMock = stubFetch(async (url) => {
    if (url.includes("/csrf/")) return response(200, { csrf_token: "csrf-for-test" });
    if (url.includes("/assets/")) {
      return response(200, {
        asset_count: 1,
        imported: [{ filename: "front.png", asset_id: "new-asset", cluster_id: "new-cluster" }],
        rejected: [],
      });
    }
    return response(200, project);
  });
  renderApp("/projects/project-demo");

  await openImportPanel();
  fireEvent.click(screen.getByRole("button", { name: "选择图片/文件夹" }));
  const input = await screen.findByLabelText("选择图片");
  fireEvent.change(input, {
    target: { files: [new File(["front"], "front.png", { type: "image/png" })] },
  });

  expect(screen.getByRole("img", { name: "待导入商品图 1" })).toBeInTheDocument();
  expect(screen.queryByText("front.png")).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "导入后整理" })[0]);
  await waitFor(() => expect(screen.queryByRole("img", { name: "待导入商品图 1" })).not.toBeInTheDocument());

  await openImportPanel();
  fireEvent.click(screen.getByRole("button", { name: "选择图片/文件夹" }));
  fireEvent.change(await screen.findByLabelText("选择图片"), {
    target: { files: [new File(["side"], "side.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getAllByRole("button", { name: "导入后整理" })[0]);
  await waitFor(() => {
    const uploads = fetchMock.mock.calls.filter(([url]) => String(url).includes("/assets/"));
    expect(uploads).toHaveLength(2);
  });
  const uploads = fetchMock.mock.calls.filter(([url]) => String(url).includes("/assets/"));
  const firstFiles = (uploads[0]![1]!.body as FormData).getAll("files") as File[];
  const secondFiles = (uploads[1]![1]!.body as FormData).getAll("files") as File[];
  expect(firstFiles.map((file) => file.name)).toEqual(["front.png"]);
  expect(secondFiles.map((file) => file.name)).toEqual(["side.png"]);
});

test("keeps pending files when the first upload request fails", async () => {
  stubFetch(async (url) => {
    if (url.includes("/csrf/")) return response(200, { csrf_token: "csrf-for-test" });
    if (url.includes("/assets/")) return response(503, { error: "上传服务暂不可用" });
    return response(200, project);
  });
  renderApp("/projects/project-demo");

  await openImportPanel();
  fireEvent.click(screen.getByRole("button", { name: "选择图片/文件夹" }));
  fireEvent.change(await screen.findByLabelText("选择图片"), {
    target: { files: [new File(["front"], "front.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getAllByRole("button", { name: "导入后整理" })[0]);

  expect(await screen.findByText("上传服务暂不可用")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "待导入商品图 1" })).toBeInTheDocument();
});

test("imports ERP SKUs in organize mode", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  await openImportPanel();
  fireEvent.change(await screen.findByLabelText("ERP SKU"), { target: { value: "LAMP-001\nLAMP-002" } });
  fireEvent.click(screen.getByRole("button", { name: "加载 SKU" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/sku-import/"))).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/sku-import/"));
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ skus: ["LAMP-001", "LAMP-002"], mode: "organize" });
});

test("marks ERP imports for automatic mode without generating before Prompt preparation", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  await openImportPanel();
  fireEvent.change(await screen.findByLabelText("ERP SKU"), { target: { value: "LAMP-001" } });
  fireEvent.click(screen.getByRole("button", { name: "加载 SKU 并自动出图" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/sku-import/"))).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/sku-import/"));
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ skus: ["LAMP-001"], mode: "auto" });
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/generate/"))).toBe(false);
});

test("renders product cards with inline identity and prompt editing", async () => {
  renderApp("/projects/project-demo");

  expect(await screen.findByRole("img", { name: "桌面护眼灯 商品参考图" })).toBeInTheDocument();
  expect(screen.getByLabelText("商品名称 桌面护眼灯")).toHaveValue("桌面护眼灯");
  fireEvent.click(screen.getByRole("button", { name: "查看 桌面护眼灯 详情" }));
  expect(screen.queryByLabelText("多图关系")).not.toBeInTheDocument();
  expect(screen.getByLabelText("商品身份")).toHaveValue("深蓝色灯头");
  expect(screen.getByLabelText("01 白底标准图 Prompt")).toHaveValue("白底标准图 prompt");
});

test("saves the editable product brief through the cluster endpoint", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  const brief = await screen.findByLabelText("创意 Brief 桌面护眼灯");
  fireEvent.change(brief, { target: { value: "更明亮的书桌场景" } });
  fireEvent.blur(brief);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/clusters/sku-lamp/"))).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/clusters/sku-lamp/"));
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ product_facts: "更明亮的书桌场景" });
});

test("starts generation for selected products and shows product and image counts", async () => {
  const fetchMock = stubFetch();
  renderApp("/projects/project-demo");

  const button = await screen.findByRole("button", { name: "正式生成（1）" });
  fireEvent.click(button);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/generate/"))).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/generate/"));
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ cluster_ids: ["sku-lamp"], slot_orders: [1, 2, 3, 4, 5, 6, 7, 8, 9] });
});

test("allows the only product to be deselected", async () => {
  renderApp("/projects/project-demo");

  fireEvent.click(await screen.findByRole("checkbox", { name: "选择 桌面护眼灯" }));

  expect(screen.getByRole("button", { name: "正式生成（0）" })).toBeDisabled();
});

test("keeps product details collapsed until requested", async () => {
  renderApp("/projects/project-demo");

  await screen.findByRole("checkbox", { name: "选择 桌面护眼灯" });
  expect(screen.queryByLabelText("商品身份")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看 桌面护眼灯 详情" }));
  expect(screen.getByLabelText("商品身份")).toHaveValue("深蓝色灯头");
});

test("keeps an unidentified product name empty instead of inserting status text", async () => {
  const unnamed = {
    ...project,
    skus: [{ ...project.skus[0], name: "", sku: "" }],
  };
  stubFetch(async (url) => {
    if (url.includes("/csrf/")) return response(200, { csrf_token: "csrf-for-test" });
    return response(200, unnamed);
  });
  renderApp("/projects/project-demo");

  const input = await screen.findByPlaceholderText("可不填，预备生成时识别");
  expect(input).toHaveValue("");
  expect(screen.queryByText("名称待确认")).not.toBeInTheDocument();
});

test("offers draggable thumbnails alongside the fixed product detail panel", async () => {
  renderApp("/projects/project-demo");

  fireEvent.click(await screen.findByRole("button", { name: "查看 桌面护眼灯 详情" }));
  expect(screen.getByRole("button", { name: "拖拽商品参考图 1" })).toHaveAttribute("aria-roledescription", "draggable");
  expect(screen.getByRole("dialog", { name: "桌面护眼灯 商品详情" })).toBeInTheDocument();
});

test("renders fifty editable product cards without expanding the workbench", async () => {
  const manyProducts = Array.from({ length: 50 }, (_, index) => ({
    ...project.skus[0],
    id: `sku-${index}`,
    name: `商品 ${index + 1}`,
    assetIds: ["asset-lamp-main"],
  }));
  const many = { ...project, skus: manyProducts };
  stubFetch(async (url) => {
    if (url.includes("/csrf/")) return response(200, { csrf_token: "csrf-for-test" });
    return response(200, many);
  });
  renderApp("/projects/project-demo");

  await screen.findByDisplayValue("商品 50");
  expect(screen.getAllByRole("textbox", { name: /^商品名称 商品/ })).toHaveLength(50);
  expect(document.querySelector(".product-card-grid")).toBeInTheDocument();
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
