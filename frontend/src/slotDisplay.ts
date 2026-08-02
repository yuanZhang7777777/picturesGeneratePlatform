import type { OutputImage } from "./types";

const slotNameMap: Record<string, string> = {
  "Seller original product photo": "原始商品图",
  "Standard white-background product hero": "标准白底产品图",
  "Standard white background product hero": "标准白底产品图",
  "Key benefit": "核心卖点图",
  "Product detail": "商品细节图",
  "Product structure": "商品结构图",
  "Function": "功能说明图",
  "Usage": "使用场景图",
  "Model or scale": "模特/比例图",
  "User or scale": "模特/比例图",
  "Size, packaging, or contents": "尺寸/包装/包含物图",
  "Packaging or contents": "包装/包含物图",
  "Marketplace conversion": "平台转化营销图",
  "Local lifestyle": "本地生活方式图",
  "Supplemental conversion": "补充转化图",
};

const fallbackByOrder: Record<number, string> = {
  1: "标准白底产品图",
  2: "核心卖点图",
  3: "商品细节图",
  4: "功能说明图",
  5: "使用场景图",
  6: "模特/比例图",
  7: "尺寸/包装/包含物图",
  8: "平台转化营销图",
  9: "补充转化图",
};

export function displaySlotName(output: Pick<OutputImage, "slot" | "slotOrder" | "name">) {
  return slotNameMap[output.slot] ?? slotNameMap[output.name] ?? output.slot ?? fallbackByOrder[output.slotOrder] ?? `第 ${output.slotOrder} 张图`;
}
