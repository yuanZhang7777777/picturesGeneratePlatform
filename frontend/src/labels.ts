export const platforms = [
  ["generic", "通用电商"],
  ["shopee", "Shopee 虾皮"],
  ["tiktok", "TikTok Shop"],
] as const;

export const commonMarkets = [
  ["SEA", "东南亚通用"],
  ["VN", "越南"],
  ["TH", "泰国"],
  ["PH", "菲律宾"],
  ["MY", "马来西亚"],
  ["SG", "新加坡"],
  ["ID", "印度尼西亚"],
] as const;

export const extraMarkets = [
  ["US", "美国"],
  ["CA", "加拿大"],
  ["AU", "澳大利亚"],
  ["IN", "印度"],
  ["AE", "阿联酋"],
  ["SA", "沙特阿拉伯"],
  ["BR", "巴西"],
  ["MX", "墨西哥"],
  ["GB", "英国"],
  ["DE", "德国"],
  ["FR", "法国"],
  ["JP", "日本"],
  ["KR", "韩国"],
] as const;

export function platformLabel(value: string | null | undefined) {
  return platforms.find(([code]) => code === value?.toLowerCase())?.[1] ?? value ?? "通用电商";
}

export function marketLabel(value: string | null | undefined) {
  return [...commonMarkets, ...extraMarkets].find(([code]) => code === value?.toUpperCase())?.[1] ?? value ?? "";
}

export function marketValue(value: string) {
  const trimmed = value.trim();
  const known = [...commonMarkets, ...extraMarkets].find(([code, label]) => code === trimmed.toUpperCase() || label === trimmed);
  return known?.[0] ?? trimmed;
}

const slotLabels: Record<string, string> = {
  hero: "白底标准图",
  main: "白底标准图",
  white_background: "白底标准图",
  angle: "第二角度/结构图",
  selling_point: "核心卖点图",
  detail: "材质或细节图",
  scene: "使用场景图",
  scale: "模特或比例展示图",
  package: "尺寸/包装/包含物图",
  conversion: "平台转化营销图",
  extra: "补充转化图",
};

export function slotLabel(value: string, order: number) {
  const normalized = value.trim().toLowerCase().replace(/[\s/-]+/g, "_");
  return slotLabels[normalized] ?? (/[\u3400-\u9fff]/.test(value) ? value : `第 ${order} 张输出图`);
}

export const stageLabels: Record<string, string> = {
  N1: "素材观察",
  N2: "商品身份",
  N3: "事实台账",
  N4: "白底主图",
  N5: "营销规划",
  N6: "单图 Prompt",
  N7: "规则校验",
};
