import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "../App";

const slots = ["hero", "angle", "selling_point", "detail", "scene", "scale", "package", "conversion", "extra"];
const product = (id: string, name: string) => ({
  id,
  name,
  sku: id,
  version: 1,
  assetIds: [`asset-${id}`],
  assets: [{ id: `asset-${id}`, name: `${id}-secret.jpg`, kind: "image", imageUrl: `/api/assets/asset-${id}/media/` }],
  facts: "可折叠",
  productFacts: "适合明亮桌面场景",
  identityLock: "保留蓝色外壳",
  identity: { product_name: name, confidence: 0.92, product_profile: { category: "桌面用品", primary_appearance: "蓝色折叠结构", shared_structure: ["折叠关节"] }, identity_lock: { must_not_change: ["蓝色外壳", "折叠关节"] } },
  brief: "柔和自然光",
  productStyle: "柔和自然光",
  preparationStatus: "preparing",
  preparation: { status: "preparing", stage: "N3", current: 3, total: 7, error: "" },
  generationProgress: { status: "idle", current: 0, total: 9 },
  overrides: { platform: null, market: null, sellerTier: null },
  effectiveConfig: { platform: "generic", market: "SEA", sellerTier: "general", size: "1:1", resolution: "1k", globalPrompt: "" },
  analysisSnapshot: {
    fact_ledger: {
      facts: [{ fact_id: "fact-1", statement: "蓝色外壳", fact_class: "observed", confidence: 0.9, evidence_refs: [], risk_level: "low", allowed_uses: ["visual_prompt"] }],
    },
  },
  prompts: slots.map((slot, index) => ({ slotOrder: index + 1, slot, text: `Prompt ${index + 1}` })),
  outputs: [],
});

const project = {
  id: "project-1",
  name: "夏日上新",
  platform: "",
  market: "",
  template: "商品基础套图",
  size: "",
  resolution: "",
  configurationStatus: "required",
  defaultConfig: { platform: "", market: "", sellerTier: "general", size: "", resolution: "", globalPrompt: "" },
  status: "draft",
  updatedAt: "2026-07-31T00:00:00Z",
  assets: [
    { id: "asset-one", name: "one-secret.jpg", kind: "image", imageUrl: "/api/assets/asset-one/media/" },
    { id: "asset-two", name: "two-secret.jpg", kind: "image", imageUrl: "/api/assets/asset-two/media/" },
  ],
  skus: [product("one", "桌面灯"), product("two", "折叠椅")],
};

function response(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function renderApp(path = "/projects/project-1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    ...render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
    ),
    queryClient,
  };
}

function stubFetch(options: { admin?: boolean; projectSnapshot?: typeof project; promptNodes?: Record<string, unknown>[] } = {}) {
  const currentProject = options.projectSnapshot ?? project;
  const promptNodes = options.promptNodes ?? [{
    id: "node-1",
    node_name: "N7.generic",
    version: "3.0.0",
    status: "retired",
    instruction: "严格检查九图 Prompt",
    user_message_template: "商品：{{ product }}",
    output_schema: { type: "object" },
    model: "deepseek-v4-pro",
    platform_scope: "generic",
    created_at: "2026-07-31T00:00:00Z",
    updated_at: "2026-07-31T00:00:00Z",
  }];
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/csrf/")) return response(200, { csrf_token: "csrf" });
    if (url === "/api/workspace/snapshot/") return response(200, { currentUser: { role: options.admin ? "admin" : "operator" }, projects: [currentProject] });
    if (url === "/api/admin/prompt-nodes/" && (!init?.method || init.method === "GET")) return options.admin ? response(200, { nodes: promptNodes }) : response(403, { error: "forbidden" });
    if (url === "/api/admin/prompt-nodes/" && init?.method === "POST") return response(201, promptNodes[0]);
    if (url === "/api/admin/prompt-nodes/publish/") return response(200, promptNodes[0]);
    return response(200, currentProject);
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

test("renders the compact toolbar with confirmed defaults and searchable extra markets", async () => {
  const fetchMock = stubFetch();
  renderApp();

  expect(await screen.findByRole("heading", { name: "夏日上新" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "通用电商" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "东南亚通用" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByLabelText("图片比例")).toHaveValue("1:1");
  expect(screen.getByLabelText("图片分辨率")).toHaveValue("1k");
  expect(screen.queryByRole("button", { name: "项目默认配置" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "更多国家" }));
  fireEvent.change(screen.getByLabelText("搜索更多国家"), { target: { value: "美国" } });
  expect(screen.getByRole("button", { name: "美国" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("搜索更多国家"), { target: { value: "法属波利尼西亚" } });
  fireEvent.click(screen.getByRole("button", { name: "使用“法属波利尼西亚”" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/projects/project-1/settings/")).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/projects/project-1/settings/");
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ market: "法属波利尼西亚" });
});

test("keeps an unsaved project style when saving fails and the project snapshot refreshes", async () => {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/csrf/")) return response(200, { csrf_token: "csrf" });
    if (url === "/api/workspace/snapshot/") return response(200, { currentUser: { role: "operator" }, projects: [project] });
    if (url === "/api/projects/project-1/settings/") return response(503, { error: "保存失败" });
    return response(200, project);
  });
  vi.stubGlobal("fetch", fetchMock);
  const { queryClient } = renderApp();
  const style = await screen.findByLabelText("项目风格");

  fireEvent.change(style, { target: { value: "本地未保存风格" } });
  fireEvent.blur(style);
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/projects/project-1/settings/")).toBe(true));

  await act(async () => {
    queryClient.setQueryData(["project", "project-1"], {
      ...project,
      defaultConfig: { ...project.defaultConfig, globalPrompt: "服务器轮询值" },
    });
  });
  expect(screen.getByLabelText("项目风格")).toHaveValue("本地未保存风格");
});

test("prepares only selected products through the explicit preparation endpoint", async () => {
  const fetchMock = stubFetch();
  renderApp();

  fireEvent.click(await screen.findByRole("checkbox", { name: "选择 折叠椅" }));
  fireEvent.click(screen.getByRole("button", { name: "预备生成（1）" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/projects/project-1/prepare/")).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/projects/project-1/prepare/");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ cluster_ids: ["one"] });
});

test("opens add product in a centered modal with the two required tabs", async () => {
  renderApp();

  fireEvent.click(await screen.findByRole("button", { name: "添加商品" }));
  const dialog = screen.getByRole("dialog", { name: "添加商品" });
  expect(dialog).toHaveClass("add-product-modal");
  expect(screen.getByRole("button", { name: "图片/文件夹" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "ERP SKU" })).toBeInTheDocument();
  expect(screen.getByLabelText("选择图片")).toHaveAttribute("multiple");
  expect(screen.getByLabelText("选择文件夹")).toHaveAttribute("webkitdirectory");
});

test("shows editable compact card fields and exact preparation progress without implementation codes", async () => {
  renderApp();

  expect(await screen.findByLabelText("商品名称 桌面灯")).toHaveValue("桌面灯");
  expect(screen.getByLabelText("商品平台 桌面灯")).toHaveDisplayValue("通用电商");
  expect(screen.getByLabelText("商品市场 桌面灯")).toHaveDisplayValue("东南亚通用");
  expect(screen.getByLabelText("创意 Brief 桌面灯")).toHaveValue("适合明亮桌面场景");
  expect(screen.getByLabelText("单品风格 桌面灯")).toHaveValue("柔和自然光");
  expect(screen.getAllByText("预备生成中 · N3 事实台账 · 3/7")).toHaveLength(2);
  expect(screen.getByLabelText("商品名称 桌面灯")).toHaveAttribute("placeholder", "可不填，预备生成时识别");
  expect(screen.getByRole("button", { name: "全选" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "取消全选" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "反选" })).toBeInTheDocument();
  expect(screen.getByLabelText("商品市场 桌面灯")).toHaveAttribute("list", "product-market-options-one");
  expect(screen.getAllByText("单品风格（选填）")).not.toHaveLength(0);
  expect(screen.queryByText("one-secret.jpg")).not.toBeInTheDocument();
  expect(screen.queryByText("generic")).not.toBeInTheDocument();
  expect(screen.queryByText("SEA")).not.toBeInTheDocument();
  expect(screen.queryByText("跟随项目")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("多图关系")).not.toBeInTheDocument();
});

test("shows product name source only for AI recognition and ERP", async () => {
  const sourceProject = {
    ...project,
    skus: [
      { ...product("ai", "AI 商品"), productNameSource: "ai" },
      { ...product("erp", "ERP 商品"), productNameSource: "erp" },
      { ...product("manual", "手工商品"), productNameSource: "manual" },
      { ...product("blank", "空来源商品"), productNameSource: "blank" },
    ],
  };
  stubFetch({ projectSnapshot: sourceProject });
  renderApp();

  expect(within(await screen.findByRole("group", { name: "AI 商品 商品卡片（可拖拽合并）" })).getByText("AI 识别，可修改")).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "ERP 商品 商品卡片（可拖拽合并）" })).getByText("来自 ERP")).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "手工商品 商品卡片（可拖拽合并）" })).queryByText(/AI 识别|来自 ERP/)).not.toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "空来源商品 商品卡片（可拖拽合并）" })).queryByText(/AI 识别|来自 ERP/)).not.toBeInTheDocument();
});

test("accepts an arbitrary per-product market and reports mixed generation failures", async () => {
  const failedProject = {
    ...project,
    skus: [{
      ...project.skus[0],
      preparationStatus: "ready",
      preparation: { status: "ready", stage: "N7", current: 7, total: 7, error: "" },
      generationProgress: { status: "completed", current: 7, completed: 7, active: 0, failed: 2, total: 9 },
    }],
  };
  const fetchMock = stubFetch({ projectSnapshot: failedProject });
  renderApp();

  const market = await screen.findByLabelText("商品市场 桌面灯");
  fireEvent.change(market, { target: { value: "法属波利尼西亚" } });
  fireEvent.blur(market);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/clusters/one/")).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/clusters/one/");
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ market_override: "法属波利尼西亚" });
  expect(screen.getByText(/有 2 张失败/)).toHaveTextContent("出图已结束 · 7/9 · 有 2 张失败");
  expect(screen.queryByText(/预备完成/)).not.toBeInTheDocument();
});

test("expands one product inline and consumes the first outside click", async () => {
  renderApp();

  fireEvent.click(await screen.findByRole("button", { name: "查看 桌面灯 详情" }));
  expect(screen.getByRole("region", { name: "桌面灯 商品详情" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: /桌面灯 商品详情/ })).not.toBeInTheDocument();
  expect(screen.getByLabelText("商品身份")).toHaveValue("保留蓝色外壳");
  expect(screen.getByRole("region", { name: "商品身份卡" })).toHaveTextContent("桌面用品");
  expect(screen.getByRole("region", { name: "商品身份卡" })).toHaveTextContent("蓝色折叠结构");
  expect(screen.getByRole("region", { name: "商品身份卡" })).toHaveTextContent("蓝色外壳");
  expect(screen.getByText("01 白底标准图 Prompt")).toBeInTheDocument();
  expect(screen.getByText("02 第二角度/结构图 Prompt")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "查看 折叠椅 详情" }));
  expect(screen.queryByRole("region", { name: "桌面灯 商品详情" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "折叠椅 商品详情" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "查看 折叠椅 详情" }));
  expect(screen.getByRole("region", { name: "折叠椅 商品详情" })).toBeInTheDocument();
});

test("hides Prompt Center from operators and exposes the Chinese administrator page", async () => {
  renderApp("/");
  expect(await screen.findByRole("heading", { name: "工作台" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Prompt 管理中心" })).not.toBeInTheDocument();
  cleanup();

  stubFetch({ admin: true });
  renderApp("/admin/prompt-center");
  expect(await screen.findByRole("heading", { name: "Prompt 管理中心" })).toBeInTheDocument();
  expect(screen.getByText("严格检查九图 Prompt")).toBeInTheDocument();
  expect(screen.getByText(/deepseek-v4-pro/)).toHaveTextContent("deepseek-v4-pro");
  expect(screen.getByText(/deepseek-v4-pro/)).toHaveTextContent("通用电商");
  expect(screen.getByRole("button", { name: "发布此版本" })).toBeInTheDocument();
});

test("does not invent model or platform scope when Prompt Center metadata is absent", async () => {
  const nodeWithoutOptionalMetadata = {
    id: "node-2",
    node_name: "N1.generic",
    version: "3.0.0",
    status: "draft",
    instruction: "观察素材",
    user_message_template: "",
    output_schema: { type: "object" },
    model: null,
    platform_scope: null,
    created_at: "2026-07-31T00:00:00Z",
    updated_at: "2026-07-31T00:00:00Z",
  };
  stubFetch({ admin: true, promptNodes: [nodeWithoutOptionalMetadata] });
  renderApp("/admin/prompt-center");

  expect(await screen.findByText("观察素材")).toBeInTheDocument();
  expect(screen.queryByText(/模型由运行时配置/)).not.toBeInTheDocument();
  expect(screen.queryByText(/共享范围/)).not.toBeInTheDocument();
});

test("creates drafts and publishes exact historical versions through the frozen admin API", async () => {
  const fetchMock = stubFetch({ admin: true });
  renderApp("/admin/prompt-center");

  fireEvent.click(await screen.findByRole("button", { name: "新建草稿" }));
  fireEvent.change(screen.getByLabelText("节点名称"), { target: { value: "N7.shopee" } });
  fireEvent.change(screen.getByLabelText("版本"), { target: { value: "3.0.1" } });
  fireEvent.change(screen.getByLabelText("系统 Prompt"), { target: { value: "检查 Shopee 九图" } });
  fireEvent.change(screen.getByLabelText("用户消息模板"), { target: { value: "商品：{{ product }}" } });
  fireEvent.change(screen.getByLabelText("输出 Schema"), { target: { value: "{\"type\":\"object\"}" } });
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url) === "/api/admin/prompt-nodes/" && init?.method === "POST")).toBe(true));
  const draftCall = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/admin/prompt-nodes/" && init?.method === "POST");
  expect(JSON.parse(String(draftCall?.[1]?.body))).toEqual({
    node_name: "N7.shopee",
    version: "3.0.1",
    instruction: "检查 Shopee 九图",
    user_message_template: "商品：{{ product }}",
    output_schema: { type: "object" },
  });

  fireEvent.click(screen.getByRole("button", { name: "发布此版本" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/admin/prompt-nodes/publish/")).toBe(true));
  const publishCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/admin/prompt-nodes/publish/");
  expect(JSON.parse(String(publishCall?.[1]?.body))).toEqual({ node_name: "N7.generic", version: "3.0.0" });
});
