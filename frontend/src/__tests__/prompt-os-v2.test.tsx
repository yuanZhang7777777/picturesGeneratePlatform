import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { updateCluster } from "../api";
import { PromptEditor } from "../components/PromptEditor";
import type { ProductSku } from "../types";

const sku: ProductSku = {
  id: "cluster-1",
  name: "便携榨汁杯",
  sku: "CUP-001",
  relationType: "single_product",
  version: 3,
  assetIds: [],
  facts: "透明杯身",
  identityLock: "保留绿色杯盖与双叶刀头",
  brief: "明亮厨房场景",
  prompts: [{ slotOrder: 1, slot: "白底标准图", text: "原始白底 Prompt" }],
  outputs: [],
  analysisSnapshot: {
    fact_ledger: {
      facts: [
        {
          fact_id: "fact.color",
          statement: "杯盖为绿色",
          fact_class: "confirmed",
          confidence: 1,
          evidence_refs: ["erp:CUP-001"],
          risk_level: "low",
          allowed_uses: ["identity", "visual_prompt"],
          review_note: "",
        },
        {
          fact_id: "fact.material",
          statement: "杯身可能为食品级塑料",
          fact_class: "inferred",
          confidence: 0.68,
          evidence_refs: ["asset:front"],
          risk_level: "high",
          allowed_uses: ["scene_planning"],
          review_note: "材质需人工确认",
        },
      ],
      review_summary: {
        confirmed_count: 1,
        observed_count: 0,
        inferred_count: 1,
        high_risk_count: 1,
      },
      blocked_claim_topics: ["certification", "medical_efficacy", "price"],
    },
    rule_gate: {
      decision: "block",
      hard_blocks: ["高风险材质推断不得进入消费者文案"],
      semantic_risks: ["“食品级”缺少确认来源"],
      warnings: ["发布前需人工复核材质"],
    },
  },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("shows Chinese product recognition details without exposing soft compliance noise", () => {
  render(<PromptEditor sku={sku} onSave={() => undefined} />);

  expect(screen.getByText("商品识别信息")).toBeInTheDocument();
  expect(screen.getByText("杯盖为绿色")).toBeInTheDocument();
  expect(screen.getByText("杯身可能为食品级塑料")).toBeInTheDocument();
  expect(screen.getByText("已确认 · 100%")).toBeInTheDocument();
  expect(screen.getByText("辅助判断 · 68%")).toBeInTheDocument();
  expect(screen.getByText("确认 1 · 图片观察 0 · 辅助判断 1")).toBeInTheDocument();
  expect(screen.queryByText(/这些内容不能靠 AI 猜出来写进图片/)).not.toBeInTheDocument();
  expect(screen.queryByText(/certification|medical_efficacy|price/)).not.toBeInTheDocument();
  expect(screen.queryByText("规则 / 合规阻断")).not.toBeInTheDocument();
  expect(screen.queryByText(/高风险材质推断不得进入消费者文案/)).not.toBeInTheDocument();
  expect(screen.queryByText(/“食品级”缺少确认来源/)).not.toBeInTheDocument();
  expect(screen.queryByText(/发布前需人工复核材质/)).not.toBeInTheDocument();
});

test("shows localized copy and visual prompt without exposing strategy labels", () => {
  render(<PromptEditor sku={{
    ...sku,
    prompts: [
      {
        slotOrder: 2,
        slot: "核心卖点图",
        text: "Create a bright kitchen scene. Show quoted visible text exactly: \"Xay mịn mỗi sáng\" and \"Mang đi là xay\".",
        displayPrompt: "通勤前把水果倒入杯中，按下就能带走；画面用明亮厨房和手部动作表现随手现榨。",
        decisionTask: "让用户想象早上随手现榨",
        creativeStrategy: { mode: "scene_ownership", mentalSimulation: "通勤前把水果倒入杯中，按下就能带走" },
        localizedCopy: {
          language: "vi",
          lines: ["Xay mịn mỗi sáng", "Mang đi là xay"],
          backTranslation: "每天早上顺滑搅拌，带上就能榨",
        },
      },
    ],
  }} onSave={() => undefined} />);

  expect(screen.getByText(/每天早上顺滑搅拌/)).toBeInTheDocument();
  expect(screen.getAllByText(/Xay mịn mỗi sáng/).length).toBeGreaterThan(0);
  expect((screen.getByLabelText("02 核心卖点图提示词") as HTMLTextAreaElement).value).toContain("通勤前");
  expect(screen.queryByText("场景代入")).not.toBeInTheDocument();
  expect(screen.queryByText(/购买任务：/)).not.toBeInTheDocument();
  expect(screen.queryByText("高级：最终生图指令")).not.toBeInTheDocument();
  expect(screen.queryByText(/localized_copy|creative_strategy|back_translation/)).not.toBeInTheDocument();
});

test("converts internal English prompts into Chinese visual prompt drafts", () => {
  render(<PromptEditor sku={{
    ...sku,
    prompts: [{
      slotOrder: 3,
      slot: "Product detail",
      text: "Create a polished ecommerce listing image. Scene: a detail fills most of the frame. Only render these exact visible text lines.",
      decisionTask: "让买家相信产品细节经得起近看",
      localizedCopy: { language: "th", lines: ["พร้อมใช้ทุกวัน"], backTranslation: "每天都适合使用" },
    }],
  }} onSave={() => undefined} />);

  const field = screen.getByLabelText("03 商品细节图提示词") as HTMLTextAreaElement;
  expect(field.value).toContain("画面：");
  expect(field.value).toContain("主体：商品清晰可见");
  expect(field.value).toContain("图片文案：พร้อมใช้ทุกวัน");
  expect(field.value).not.toContain("购买任务：");
  expect(field.value).not.toContain("Create a polished ecommerce");
  expect(screen.getAllByText(/พร้อมใช้ทุกวัน/).length).toBeGreaterThan(0);
});

test("uses placeholders instead of editable fake prompt text before preparation", () => {
  render(<PromptEditor sku={{ ...sku, prompts: [] }} onSave={() => undefined} />);

  const field = screen.getByLabelText("02 核心卖点图提示词") as HTMLTextAreaElement;
  expect(field.value).toBe("");
  expect(field.placeholder).toContain("预备生成后显示");
});

test("posts edited prompts as a structured snake-case array", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ csrf_token: "csrf" }) })
    .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: "cluster-1", version: 4 }) });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <PromptEditor
      sku={sku}
      onSave={(payload) => {
        void updateCluster(sku.id, sku.version, payload);
      }}
    />,
  );

  fireEvent.change(screen.getByLabelText("01 白底标准图提示词"), {
    target: { value: "纯白背景，保留绿色杯盖" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存提示词" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toMatchObject({
    expected_version: 3,
    prompts: [{ slot_order: 1, prompt: "纯白背景，保留绿色杯盖" }],
  });
});

test("keeps dirty identity and prompt drafts across a polling snapshot", () => {
  const view = render(<PromptEditor sku={sku} onSave={() => undefined} />);

  fireEvent.change(screen.getByLabelText("01 白底标准图提示词"), { target: { value: "保留本地白底 Prompt" } });

  view.rerender(<PromptEditor sku={{
    ...sku,
    relationType: "variant_group",
    identityLock: "服务器身份锁",
    brief: "服务器 Brief",
    prompts: [{ slotOrder: 1, slot: "白底标准图", text: "服务器 Prompt" }],
  }} onSave={() => undefined} />);

  expect(screen.getByLabelText("01 白底标准图提示词")).toHaveValue("保留本地白底 Prompt");
});

test("keeps a successful prompt save while the parent still renders the old SKU snapshot", async () => {
  const onSave = vi.fn().mockResolvedValue({ id: "cluster-1", version: 4 });
  const view = render(<PromptEditor sku={sku} onSave={onSave} />);

  fireEvent.change(screen.getByLabelText("01 白底标准图提示词"), { target: { value: "已保存白底提示词" } });
  fireEvent.click(screen.getByRole("button", { name: "保存提示词" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  view.rerender(<PromptEditor sku={sku} onSave={onSave} />);

  expect(screen.getByLabelText("01 白底标准图提示词")).toHaveValue("已保存白底提示词");
});

test("adopts the acknowledged SKU snapshot as the new prompt baseline", async () => {
  const onSave = vi.fn().mockResolvedValue({ id: "cluster-1", version: 4 });
  const view = render(<PromptEditor sku={sku} onSave={onSave} />);

  fireEvent.change(screen.getByLabelText("01 白底标准图提示词"), { target: { value: "已保存白底提示词" } });
  fireEvent.click(screen.getByRole("button", { name: "保存提示词" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));

  view.rerender(<PromptEditor sku={{ ...sku, version: 4, prompts: [{ slotOrder: 1, slot: "白底标准图", text: "服务器确认提示词" }] }} onSave={onSave} />);
  expect(screen.getByLabelText("01 白底标准图提示词")).toHaveValue("服务器确认提示词");

  view.rerender(<PromptEditor sku={{ ...sku, version: 5, prompts: [{ slotOrder: 1, slot: "白底标准图", text: "后续远端提示词" }] }} onSave={onSave} />);
  expect(screen.getByLabelText("01 白底标准图提示词")).toHaveValue("后续远端提示词");
});
