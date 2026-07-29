import { expect, test } from "vitest";
import { moveAssetToSku } from "../workspace";

test("moves a product angle into the selected SKU while keeping all assets", () => {
  const result = moveAssetToSku(
    [
      { id: "sku-a", assetIds: ["asset-a"] },
      { id: "sku-b", assetIds: ["asset-b"] },
    ],
    "asset-b",
    "sku-a",
  );

  expect(result).toEqual([
    { id: "sku-a", assetIds: ["asset-a", "asset-b"] },
    { id: "sku-b", assetIds: [] },
  ]);
});
