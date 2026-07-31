import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "../api";
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

test("combines card edits into one explicit versioned save", async () => {
  const onSave = vi.fn().mockResolvedValue({ id: "sku-1", version: 2 });
  render(<ProductCard sku={sku} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={onSave} onReload={vi.fn()} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  fireEvent.change(screen.getByLabelText("商品名称"), { target: { value: "新名称" } });
  fireEvent.click(screen.getByRole("button", { name: "更多设置" }));
  fireEvent.change(screen.getByLabelText("商品平台"), { target: { value: "tiktok" } });
  fireEvent.change(screen.getByLabelText("商品国家"), { target: { value: "US" } });
  fireEvent.change(screen.getByLabelText("商品补充信息"), { target: { value: "展示使用方式" } });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave).toHaveBeenCalledWith({ name: "新名称", platform_override: "tiktok", market_override: "US", prompt_override: "展示使用方式" }, 1);
});

test("preserves a dirty draft and retries with the refreshed version after a 409", async () => {
  const onSave = vi.fn().mockRejectedValueOnce(new ApiError(409, "版本已更新"));
  const onReload = vi.fn().mockResolvedValue(undefined);
  const view = render(<ProductCard sku={sku} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={onSave} onReload={onReload} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  fireEvent.change(screen.getByLabelText("商品名称"), { target: { value: "仍要保留的名称" } });
  fireEvent.click(screen.getByRole("button", { name: "更多设置" }));
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  expect(await screen.findByText("商品信息已更新，请保留修改后重试")).toBeInTheDocument();
  expect(onReload).toHaveBeenCalledTimes(1);

  view.rerender(<ProductCard sku={{ ...sku, version: 2 }} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={onSave.mockResolvedValueOnce({ id: "sku-1", version: 3 })} onReload={onReload} onDeleteAsset={() => undefined} onDelete={() => undefined} />);
  expect(screen.getByLabelText("商品名称")).toHaveValue("仍要保留的名称");
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
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

test("shows seller tier override and a precise blocked action", async () => {
  const onSave = vi.fn().mockResolvedValue({ id: "sku-1", version: 2 });
  render(<ProductCard sku={sku} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={onSave} onReload={vi.fn()} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  expect(screen.getByText("请确认商品身份或补充商品名称")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "更多设置" }));
  expect(screen.getByText("店铺类型：普通店（跟随项目）")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("商品店铺类型"), { target: { value: "mall" } });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledWith({ seller_tier_override: "mall" }, 1));
  fireEvent.click(screen.getByRole("button", { name: "恢复跟随项目" }));
  await waitFor(() => expect(onSave).toHaveBeenLastCalledWith({ platform_override: null, market_override: null, seller_tier_override: null }, 2));
});

test("shows TikTok as a platform-fixed ordinary store even if a raw override says mall", () => {
  const tiktokSku = { ...sku, overrides: { platform: "tiktok", market: "US", sellerTier: "mall" as const }, effectiveConfig: { ...sku.effectiveConfig!, platform: "tiktok", market: "US", sellerTier: "general" as const } };
  render(<ProductCard sku={tiktokSku} assets={tiktokSku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={vi.fn()} onReload={vi.fn()} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  fireEvent.click(screen.getByRole("button", { name: "更多设置" }));
  expect(screen.getByText("店铺类型：普通店（平台规则固定）")).toBeInTheDocument();
  expect(screen.queryByLabelText("商品店铺类型")).not.toBeInTheDocument();
  expect(screen.getByText("TikTok Shop 店铺类型固定为普通店，不能单独修改。")).toBeInTheDocument();
});

test("does not submit a Mall override after switching the product to TikTok", async () => {
  const onSave = vi.fn().mockResolvedValue({ id: "sku-1", version: 2 });
  render(<ProductCard sku={sku} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={onSave} onReload={vi.fn()} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  fireEvent.click(screen.getByRole("button", { name: "更多设置" }));
  fireEvent.change(screen.getByLabelText("商品店铺类型"), { target: { value: "mall" } });
  fireEvent.change(screen.getByLabelText("商品平台"), { target: { value: "tiktok" } });
  expect(screen.queryByLabelText("商品店铺类型")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

  await waitFor(() => expect(onSave).toHaveBeenCalledWith({ platform_override: "tiktok" }, 1));
});

test("keeps the card square and hides detailed fields until its drawer opens", () => {
  const view = render(<ProductCard sku={sku} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={vi.fn()} onReload={vi.fn()} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  expect(view.container.querySelector(".product-card")).toHaveClass("aspect-square");
  expect(screen.queryByLabelText("商品平台")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("商品补充信息")).not.toBeInTheDocument();
});

test("closes the product details drawer with Escape and exposes dialog semantics", () => {
  render(<ProductCard sku={sku} assets={sku.assets!} mergeableAssets={[]} selected onSelect={() => undefined} onMerge={() => undefined} onSave={vi.fn()} onReload={vi.fn()} onDeleteAsset={() => undefined} onDelete={() => undefined} />);

  fireEvent.click(screen.getByRole("button", { name: "更多设置" }));
  expect(screen.getByRole("dialog", { name: "旧名称 商品详情" })).toHaveAttribute("aria-modal", "true");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "旧名称 商品详情" })).not.toBeInTheDocument();
});
