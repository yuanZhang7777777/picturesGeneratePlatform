import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError } from "../api";
import { collectDroppedFiles, ImportPanel } from "../components/ImportPanel";
import { ProductCard } from "../components/ProductCard";
import type { ProductSku } from "../types";

const sku: ProductSku = {
  id: "sku-1", name: "旧名称", sku: "SKU-1", relationType: "single_product", assetIds: ["asset-1"],
  assets: [{ id: "asset-1", name: "hidden.png", kind: "image", imageUrl: "/image" }],
  facts: "旧补充信息", productFacts: "旧补充信息", productStyle: "旧风格", identityLock: "", brief: "旧风格", version: 1, preparationStatus: "blocked",
  preparation: { status: "blocked", stage: "N2", current: 2, total: 7, error: "请确认商品身份" },
  overrides: { platform: null, market: null, sellerTier: null },
  effectiveConfig: { platform: "shopee", market: "SG", sellerTier: "general", size: "1:1", resolution: "1k", globalPrompt: "" },
  analysisSnapshot: { readiness: { status: "blocked", code: "identity_needs_input" } },
  prompts: [{ slotOrder: 1, slot: "hero", text: "白底图 Prompt" }],
  outputs: [],
};

const props = {
  assets: sku.assets!, mergeableAssets: [], selected: true, onSelect: () => undefined, onMerge: () => undefined,
  onReload: vi.fn(), onDeleteAsset: () => undefined, onDelete: () => undefined,
};

afterEach(cleanup);

test("combines dirty card fields into one explicit versioned save", async () => {
  const onSave = vi.fn().mockResolvedValue({ id: "sku-1", version: 2 });
  render(<ProductCard {...props} sku={sku} onSave={onSave} />);

  fireEvent.change(screen.getByLabelText("商品名称 旧名称"), { target: { value: "新名称" } });
  fireEvent.change(screen.getByLabelText("商品平台 新名称"), { target: { value: "tiktok" } });
  fireEvent.change(screen.getByLabelText("商品市场 新名称"), { target: { value: "VN" } });
  fireEvent.change(screen.getByLabelText("创意 Brief 新名称"), { target: { value: "展示使用方式" } });
  fireEvent.change(screen.getByLabelText("单品风格 新名称"), { target: { value: "自然光" } });
  fireEvent.blur(screen.getByLabelText("单品风格 新名称"));

  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave).toHaveBeenCalledWith({ name: "新名称", product_facts: "展示使用方式", prompt_override: "自然光", platform_override: "tiktok", market_override: "VN" }, 1);
});

test("preserves a dirty card draft and retries with the refreshed version after a 409", async () => {
  const onSave = vi.fn().mockRejectedValueOnce(new ApiError(409, "版本已更新"));
  const onReload = vi.fn().mockResolvedValue(undefined);
  const view = render(<ProductCard {...props} sku={sku} onSave={onSave} onReload={onReload} />);

  fireEvent.change(screen.getByLabelText("商品名称 旧名称"), { target: { value: "仍要保留的名称" } });
  fireEvent.blur(screen.getByLabelText("商品名称 仍要保留的名称"));
  expect(await screen.findByText("商品信息已更新，请保留修改后重试")).toBeInTheDocument();
  expect(onReload).toHaveBeenCalledTimes(1);

  view.rerender(<ProductCard {...props} sku={{ ...sku, version: 2 }} onSave={onSave.mockResolvedValueOnce({ id: "sku-1", version: 3 })} onReload={onReload} />);
  expect(screen.getByLabelText("商品名称 仍要保留的名称")).toHaveValue("仍要保留的名称");
  fireEvent.blur(screen.getByLabelText("商品名称 仍要保留的名称"));
  await waitFor(() => expect(onSave).toHaveBeenLastCalledWith({ name: "仍要保留的名称" }, 2));
});

test("keeps failed ERP SKUs visible after a partial import", async () => {
  render(<ImportPanel onUpload={vi.fn()} onImported={vi.fn()} onSkuImport={vi.fn().mockResolvedValue({
    imported: 1, failed: 1, items: [
      { sku: "OK-1", status: "imported", errorCode: null },
      { sku: "MISSING", status: "failed", errorCode: "sku_not_found" },
    ],
  })} />);

  fireEvent.click(screen.getByRole("button", { name: "ERP SKU" }));
  fireEvent.change(screen.getByLabelText("ERP SKU"), { target: { value: "OK-1\nMISSING" } });
  fireEvent.click(screen.getByRole("button", { name: "导入后整理" }));

  expect(await screen.findByText("MISSING：SKU 不存在或无可用商品图片")).toBeInTheDocument();
  expect(screen.getByLabelText("ERP SKU")).toHaveValue("MISSING");
});

test("accepts multiple dropped images when the browser has no folder entries", async () => {
  const first = new File(["one"], "one.png", { type: "image/png" });
  const second = new File(["two"], "two.png", { type: "image/png" });
  const files = await collectDroppedFiles({ items: [], files: [first, second] } as unknown as DataTransfer);
  expect(files.map((file) => file.name)).toEqual(["one.png", "two.png"]);
});

test("shows exact blocked progress and no raw implementation configuration", () => {
  render(<ProductCard {...props} sku={sku} onSave={vi.fn()} />);
  expect(screen.getByText("预备受阻 · 请确认商品身份")).toBeInTheDocument();
  expect(screen.getByLabelText("商品平台 旧名称")).toHaveDisplayValue("Shopee 虾皮");
  expect(screen.getByLabelText("商品市场 旧名称")).toHaveDisplayValue("新加坡");
  expect(screen.queryByText("跟随项目")).not.toBeInTheDocument();
});

test("keeps the compact product card square with editable fields", () => {
  const view = render(<ProductCard {...props} sku={sku} onSave={vi.fn()} />);
  expect(view.container.querySelector(".product-card")).toHaveClass("aspect-square");
  expect(screen.getByLabelText("商品平台 旧名称")).toBeInTheDocument();
  expect(screen.getByLabelText("创意 Brief 旧名称")).toBeInTheDocument();
});

test("renders product details inline rather than as a modal drawer", () => {
  render(<ProductCard {...props} sku={sku} expanded onSave={vi.fn()} />);
  expect(screen.getByRole("region", { name: "旧名称 商品详情" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "旧名称 商品详情" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("商品身份")).toBeInTheDocument();
});
