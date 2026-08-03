import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "../App";
import type { ProductSku, Project } from "../types";

const slots = ["hero", "angle", "selling_point", "detail", "scene", "scale", "package", "conversion", "extra"];
const product = (id: string, name: string): ProductSku => ({
  id,
  name,
  productNameSource: "manual" as const,
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

const project: Project = {
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
  expect(screen.getByLabelText("项目国家")).toHaveDisplayValue("东南亚通用");
  expect(screen.getByLabelText("图片比例")).toHaveValue("1:1");
  expect(screen.getByLabelText("图片分辨率")).toHaveValue("1k");
  expect(screen.queryByRole("button", { name: "项目默认配置" })).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("项目国家"), { target: { value: "US" } });
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/projects/project-1/settings/")).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/projects/project-1/settings/");
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ market: "US" });
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
  const style = await screen.findByLabelText("项目风格提示词");

  fireEvent.change(style, { target: { value: "本地未保存风格" } });
  fireEvent.blur(style);
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/projects/project-1/settings/")).toBe(true));

  await act(async () => {
    queryClient.setQueryData(["project", "project-1"], {
      ...project,
      defaultConfig: { ...project.defaultConfig, globalPrompt: "服务器轮询值" },
    });
  });
  expect(screen.getByLabelText("项目风格提示词")).toHaveValue("本地未保存风格");
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

test("keeps preparation click responsive while a card save is pending", async () => {
  let releaseSave: (() => void) | undefined;
  const fetchMock = stubFetch({ projectSnapshot: project });
  fetchMock.mockImplementation(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/csrf/")) return response(200, { csrf_token: "csrf" });
    if (url === "/api/projects/project-1/snapshot/") return response(200, project);
    if (url === "/api/workspace/snapshot/") return response(200, { currentUser: { role: "operator" }, projects: [project] });
    if (url === "/api/clusters/one/") {
      return new Promise((resolve) => {
        releaseSave = () => resolve(response(200, { id: "one", version: 2 }));
      });
    }
    if (url === "/api/projects/project-1/prepare/") return response(200, { items: [] });
    return response(200, project);
  });
  renderApp();

  const supplement = await screen.findByLabelText("补充信息 桌面灯");
  fireEvent.change(supplement, { target: { value: "新的补充信息" } });
  fireEvent.blur(supplement);
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/clusters/one/")).toBe(true));

  fireEvent.click(screen.getByRole("button", { name: "预备生成（2）" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/projects/project-1/prepare/")).toBe(true));
  releaseSave?.();
});

test("pauses selected products from the floating action bar", async () => {
  const fetchMock = stubFetch();
  renderApp();

  fireEvent.click(await screen.findByRole("button", { name: "暂停所选（2）" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/projects/project-1/pause/")).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/projects/project-1/pause/");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ cluster_ids: ["one", "two"] });
});

test("pauses one product from its card", async () => {
  const fetchMock = stubFetch();
  renderApp();

  fireEvent.click(await screen.findByRole("button", { name: "暂停 桌面灯" }));

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/projects/project-1/pause/")).toBe(true));
  const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/projects/project-1/pause/");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ cluster_ids: ["one"] });
});

test("shows the add-product panel inline with organize as the primary import action", async () => {
  renderApp();

  expect(await screen.findByRole("heading", { name: "夏日上新" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "添加商品" })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "添加商品" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("添加商品面板")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "图片/文件夹" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("ERP SKU")).toBeInTheDocument();
  expect(screen.getByLabelText("选择图片")).toHaveAttribute("multiple");
  expect(screen.getByLabelText("选择文件夹")).toHaveAttribute("webkitdirectory");
  expect(screen.getByRole("button", { name: "选择图片/文件夹" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "加载 SKU" })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "导入后整理" })[0]).toHaveClass("primary-button");
  expect(screen.getAllByRole("button", { name: "导入并自动出图" })[0]).toHaveClass("secondary-button");
});

test("shows editable compact card fields and exact preparation progress without implementation codes", async () => {
  renderApp();

  expect(await screen.findByLabelText("商品名称 桌面灯")).toHaveValue("桌面灯");
  expect((screen.getByLabelText("补充信息 桌面灯") as HTMLTextAreaElement).value).toContain("适合明亮桌面场景");
  expect((screen.getByLabelText("补充信息 桌面灯") as HTMLTextAreaElement).value).toContain("保留蓝色外壳");
  expect(screen.getByLabelText("项目国家")).toBeInTheDocument();
  expect(screen.getByLabelText("商品平台 桌面灯")).toHaveDisplayValue("跟随项目");
  expect(screen.getByLabelText("商品国家 桌面灯")).toHaveDisplayValue("跟随项目");
  expect(screen.queryByLabelText("单品风格 桌面灯")).not.toBeInTheDocument();
  expect(screen.getAllByText("正在整理商品信息 · 3/7")).toHaveLength(2);
  expect(screen.getAllByRole("progressbar", { name: "预备生成进度" })).not.toHaveLength(0);
  expect(screen.getByLabelText("商品名称 桌面灯")).toHaveAttribute("placeholder", "可不填，预备生成时识别");
  expect(screen.getByRole("button", { name: "全选" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "取消全选" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "反选" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "暂停所选（2）" })).toBeInTheDocument();
  expect(screen.getByLabelText("滚动常驻生成动作")).toHaveTextContent("预备生成（2）");
  expect(screen.getByLabelText("滚动常驻生成动作")).toHaveTextContent("正式生成（2）");
  expect(screen.queryByText("单品风格（选填）")).not.toBeInTheDocument();
  expect(screen.queryByText("one-secret.jpg")).not.toBeInTheDocument();
  expect(screen.queryByText("generic")).not.toBeInTheDocument();
  expect(screen.queryByText("SEA")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("多图关系")).not.toBeInTheDocument();
});

test("shows product name source only for AI recognition and ERP", async () => {
  const sourceProject = {
    ...project,
    skus: [
      { ...product("ai", "AI 商品"), productNameSource: "ai" as const },
      { ...product("erp", "ERP 商品"), productNameSource: "erp" as const },
      { ...product("manual", "手工商品"), productNameSource: "manual" as const },
      { ...product("blank", "空来源商品"), productNameSource: "blank" as const },
    ],
  };
  stubFetch({ projectSnapshot: sourceProject });
  renderApp();

  expect(within(await screen.findByRole("group", { name: "AI 商品 商品卡片（可拖拽合并）" })).getByText("AI 识别，可修改")).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "ERP 商品 商品卡片（可拖拽合并）" })).getByText("来自 ERP")).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "手工商品 商品卡片（可拖拽合并）" })).queryByText(/AI 识别|来自 ERP/)).not.toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "空来源商品 商品卡片（可拖拽合并）" })).queryByText(/AI 识别|来自 ERP/)).not.toBeInTheDocument();
});

test("reports mixed generation failures without per-product market overrides", async () => {
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

  await screen.findByLabelText("商品名称 桌面灯");
  expect(screen.getByText(/有 2 张失败/)).toHaveTextContent("出图已结束 · 7/9 · 有 2 张失败");
  expect(screen.queryByText(/预备完成/)).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/clusters/one/")).toBe(false);
});

test("hides technical preparation errors from operators", async () => {
  const failedProject = {
    ...project,
    skus: [{
      ...project.skus[0],
      preparationStatus: "failed",
      preparation: { status: "failed", stage: "failed", current: 0, total: 7, error: "evidence_refs contains an unknown evidence reference" },
    }],
  };
  stubFetch({ projectSnapshot: failedProject });
  renderApp();

  expect(await screen.findByText(/预备未完成 · 提示词生成失败，请重试预备生成/)).toBeInTheDocument();
  expect(screen.queryByText(/evidence_refs/)).not.toBeInTheDocument();
});

test("shows AI-recognized English product info as Chinese operator text", async () => {
  stubFetch({
    projectSnapshot: {
      ...project,
      skus: [{
        ...product("english", "Copper-bowl wooden-handled cutlery"),
        productNameSource: "ai",
        facts: "visible wooden-handled spoons with tray; tray material resembles pressed pulp/cardboard",
        productFacts: "visible wooden-handled spoons with tray; tray material resembles pressed pulp/cardboard",
        identityLock: "wooden handled cutlery; tray set",
      }],
    },
  });
  renderApp();

  const card = await screen.findByRole("group", { name: /木柄餐具套装 商品卡片/ });
  expect(within(card).getByLabelText("商品名称 木柄餐具套装")).toHaveValue("木柄餐具套装");
  expect((within(card).getByLabelText("补充信息 木柄餐具套装") as HTMLTextAreaElement).value).toContain("木柄餐具套装");
  expect(within(card).queryByDisplayValue(/Copper-bowl|visible wooden/i)).not.toBeInTheDocument();
});

test("shows mixed English recognized plush identity as Chinese operator text", async () => {
  stubFetch({
    projectSnapshot: {
      ...project,
      skus: [{
        ...product("plush", "Yellow plush toy"),
        productNameSource: "ai",
        facts: "Yellow plush toy, round black eyes, plush texture\nMain appearance:\nStyle/Color: Yellow plush toy\nStyle/Requirements:\nIdentity maintained: yellow plush暗黑风格",
        productFacts: "Yellow plush toy, round black eyes, plush texture\nMain appearance:\nStyle/Color: Yellow plush toy\nStyle/Requirements:\nIdentity maintained: yellow plush暗黑风格",
        identityLock: "yellow plush暗黑风格",
        identity: {
          product_name: "Yellow plush toy",
          confidence: 0.9,
          product_profile: { category: "plush toy", primary_appearance: "Yellow plush toy, round black eyes, plush texture", shared_structure: ["plush texture", "round black eyes"] },
          identity_lock: { must_not_change: ["yellow plush", "round black eyes"] },
          target_appearances: [],
        },
      }],
    },
  });
  renderApp();

  const card = await screen.findByRole("group", { name: /黄色毛绒玩偶 商品卡片/ });
  const supplement = within(card).getByLabelText("补充信息 黄色毛绒玩偶") as HTMLTextAreaElement;
  expect(supplement.value).toContain("黄色毛绒玩偶");
  expect(supplement.value).toContain("圆形黑眼睛");
  expect(supplement.value).toContain("毛绒质感");
  expect(supplement.value).toContain("暗黑风格");
  expect(supplement.value).not.toMatch(/Yellow plush|Main appearance|Style\/Color|Identity maintained/i);
});

test("fills an empty product name from a later AI identity snapshot without overwriting edited supplement", async () => {
  const blankProject = {
    ...project,
    skus: [{
      ...product("blank", ""),
      name: "",
      productFacts: "",
      facts: "",
      identity: undefined,
      productNameSource: "blank" as const,
    }],
  };
  stubFetch({ projectSnapshot: blankProject });
  const { queryClient } = renderApp();

  const supplement = await screen.findByLabelText("补充信息 未命名商品");
  fireEvent.change(supplement, { target: { value: "人工补充不要覆盖" } });
  await act(async () => {
    queryClient.setQueryData(["project", "project-1"], {
      ...blankProject,
      skus: [{
        ...blankProject.skus[0],
        name: "Yellow plush toy",
        productNameSource: "ai" as const,
        facts: "Yellow plush toy, round black eyes",
        productFacts: "Yellow plush toy, round black eyes",
        identity: {
          product_name: "Yellow plush toy",
          confidence: 0.92,
          product_profile: { category: "plush toy", primary_appearance: "Yellow plush toy, round black eyes" },
        },
        version: 2,
      }],
    });
  });

  expect(await screen.findByLabelText("商品名称 黄色毛绒玩偶")).toHaveValue("黄色毛绒玩偶");
  expect(screen.getByLabelText("补充信息 黄色毛绒玩偶")).toHaveValue("人工补充不要覆盖");
});

test("opens one product in a fixed side panel and consumes the first outside click", async () => {
  renderApp();

  fireEvent.click(await screen.findByRole("button", { name: "桌面灯 详情" }));
  expect(screen.getByRole("dialog", { name: "桌面灯 商品详情" })).toBeInTheDocument();
  expect((screen.getAllByLabelText("补充信息 桌面灯")[0] as HTMLTextAreaElement).value).toContain("适合明亮桌面场景");
  expect(screen.queryByLabelText("商品身份")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("主要外观")).not.toBeInTheDocument();
  expect(screen.getByText("01 标准白底产品图提示词")).toBeInTheDocument();
  expect(screen.getByText("02 核心卖点图提示词")).toBeInTheDocument();
  expect(screen.queryByText(/第 1 张输出图提示词/)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "折叠椅 详情" }));
  expect(screen.queryByRole("dialog", { name: "桌面灯 商品详情" })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "折叠椅 商品详情" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "折叠椅 详情" }));
  expect(screen.getByRole("dialog", { name: "折叠椅 商品详情" })).toBeInTheDocument();
});

test("labels Shopee VN source-photo slots without a duplicate white-background title", async () => {
  const vnSlots = [
    "Seller original product photo",
    "Standard white background product hero",
    "Product structure",
    "Product detail",
    "Usage",
    "User or scale",
    "Packaging or contents",
    "Local lifestyle",
    "Supplemental conversion",
  ];
  stubFetch({
    projectSnapshot: {
      ...project,
      platform: "shopee",
      market: "VN",
      defaultConfig: { platform: "shopee", market: "VN", sellerTier: "general", size: "1:1", resolution: "1k", globalPrompt: "" },
      skus: [{
        ...product("vn", "越南商品"),
        prompts: vnSlots.map((slot, index) => ({ slotOrder: index + 1, slot, text: "", readOnly: index === 0 })),
      }],
    },
  });
  renderApp();

  fireEvent.click(await screen.findByRole("button", { name: "越南商品 详情" }));

  expect(screen.queryByText("01 原始商品图提示词")).not.toBeInTheDocument();
  expect(screen.getByText("02 标准白底产品图提示词")).toBeInTheDocument();
  expect(screen.getByText("03 商品结构图提示词")).toBeInTheDocument();
  expect(screen.getByText("08 本地生活方式图提示词")).toBeInTheDocument();
  expect(screen.queryByText("01 标准白底产品图提示词")).not.toBeInTheDocument();
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
