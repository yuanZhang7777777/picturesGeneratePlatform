import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { collectDroppedFiles, ImportPanel } from "../components/ImportPanel";
import { ProductCard } from "../components/ProductCard";
import type { ProductSku } from "../types";

const sku: ProductSku = {
  id: "sku-1", name: "旧名称", sku: "SKU-1", relationType: "single_product", assetIds: ["asset-1"],
  assets: [{ id: "asset-1", name: "hidden.png", kind: "image", imageUrl: "/image" }],
  facts: "", identityLock: "", brief: "旧要求", version: 1, preparationStatus: "blocked",
  overrides: { platform: null, market: null, sellerTier: null },
  effectiveConfig: { platform: "shopee", market: "SG", sellerTier: "general", size: "1:1", resolution: "1k", globalPrompt: "" },
  analysisSnapshot: { readiness: { status: "blocked", code: "identity_needs_input" } },
  outputs: [],
};

afterEach(cleanup);

test("combines card edits into one explicit versioned save", () => {
  const onSave = vi.fn();
  render(<ProductCard sku={sku} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={onSave} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  fireEvent.change(screen.getByLabelText("商品名称"), { target: { value: "新名称" } });
  fireEvent.change(screen.getByLabelText("商品平台"), { target: { value: "tiktok" } });
  fireEvent.change(screen.getByLabelText("商品国家"), { target: { value: "US" } });
  fireEvent.change(screen.getByLabelText("商品补充信息"), { target: { value: "展示使用方式" } });
  fireEvent.click(screen.getByRole("button", { name: "更多设置" }));
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

  expect(onSave).toHaveBeenCalledTimes(1);
  expect(onSave).toHaveBeenCalledWith({ name: "新名称", platform_override: "tiktok", market_override: "US", prompt_override: "展示使用方式" });
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

test("shows seller tier override and a precise blocked action", () => {
  const onSave = vi.fn();
  render(<ProductCard sku={sku} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={onSave} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  expect(screen.getByText("请确认商品身份或补充商品名称")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "更多设置" }));
  fireEvent.change(screen.getByLabelText("商品店铺类型"), { target: { value: "mall" } });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  expect(onSave).toHaveBeenCalledWith({ seller_tier_override: "mall" });
  fireEvent.click(screen.getByRole("button", { name: "恢复跟随项目" }));
  expect(onSave).toHaveBeenLastCalledWith({ platform_override: null, market_override: null, seller_tier_override: null });
});

test("closes the product details drawer with Escape and exposes dialog semantics", () => {
  render(<ProductCard sku={sku} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={() => undefined} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  fireEvent.click(screen.getByRole("button", { name: "更多设置" }));
  expect(screen.getByRole("dialog", { name: "旧名称 商品详情" })).toHaveAttribute("aria-modal", "true");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "旧名称 商品详情" })).not.toBeInTheDocument();
});
