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

test("shows the Prompt OS fact ledger and compliance block summary", () => {
  render(<PromptEditor sku={sku} onSave={() => undefined} />);

  expect(screen.getByText("推断台账")).toBeInTheDocument();
  expect(screen.getByText("杯盖为绿色")).toBeInTheDocument();
  expect(screen.getByText("杯身可能为食品级塑料")).toBeInTheDocument();
  expect(screen.getByText("已确认 · 100% · 低风险")).toBeInTheDocument();
  expect(screen.getByText("合理推断 · 68% · 高风险")).toBeInTheDocument();
  expect(screen.getByText("确认 1 · 观察 0 · 推断 1 · 高风险 1")).toBeInTheDocument();
  expect(screen.getByText(/这些内容不能靠 AI 猜出来写进图片：认证\/奖项、医疗\/疗效、价格\/折扣/)).toBeInTheDocument();
  expect(screen.queryByText(/certification|medical_efficacy|price/)).not.toBeInTheDocument();
  expect(screen.getByText("规则 / 合规阻断")).toBeInTheDocument();
  expect(screen.getByText(/高风险材质推断不得进入消费者文案/)).toBeInTheDocument();
  expect(screen.getByText(/“食品级”缺少确认来源/)).toBeInTheDocument();
  expect(screen.getByText(/发布前需人工复核材质/)).toBeInTheDocument();
});

test("shows marketing strategy, localized copy and final prompt without JSON field names", () => {
  render(<PromptEditor sku={{
    ...sku,
    prompts: [
      {
        slotOrder: 2,
        slot: "核心卖点图",
        text: "Create a bright kitchen scene. Show quoted visible text exactly: \"Xay mịn mỗi sáng\" and \"Mang đi là xay\".",
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

  expect(screen.getByText("场景代入")).toBeInTheDocument();
  expect(screen.getByText(/让用户想象早上随手现榨/)).toBeInTheDocument();
  expect(screen.getByText(/每天早上顺滑搅拌/)).toBeInTheDocument();
  expect(screen.getAllByText(/Xay mịn mỗi sáng/).length).toBeGreaterThan(0);
  expect((screen.getByLabelText("02 核心卖点图 Prompt") as HTMLTextAreaElement).value).toContain("Create a bright kitchen scene");
  expect(screen.queryByText(/localized_copy|creative_strategy|back_translation/)).not.toBeInTheDocument();
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

  fireEvent.change(screen.getByLabelText("01 白底标准图 Prompt"), {
    target: { value: "纯白背景，保留绿色杯盖" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存 Prompt" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toMatchObject({
    expected_version: 3,
    prompts: [{ slot_order: 1, prompt: "纯白背景，保留绿色杯盖" }],
  });
});

test("keeps dirty identity and prompt drafts across a polling snapshot", () => {
  const view = render(<PromptEditor sku={sku} onSave={() => undefined} />);

  fireEvent.change(screen.getByLabelText("商品身份"), { target: { value: "保留本地身份锁" } });
  fireEvent.change(screen.getByLabelText("01 白底标准图 Prompt"), { target: { value: "保留本地白底 Prompt" } });

  view.rerender(<PromptEditor sku={{
    ...sku,
    relationType: "variant_group",
    identityLock: "服务器身份锁",
    brief: "服务器 Brief",
    prompts: [{ slotOrder: 1, slot: "白底标准图", text: "服务器 Prompt" }],
  }} onSave={() => undefined} />);

  expect(screen.getByLabelText("商品身份")).toHaveValue("保留本地身份锁");
  expect(screen.getByLabelText("01 白底标准图 Prompt")).toHaveValue("保留本地白底 Prompt");
});

test("keeps a successful prompt save while the parent still renders the old SKU snapshot", async () => {
  const onSave = vi.fn().mockResolvedValue({ id: "cluster-1", version: 4 });
  const view = render(<PromptEditor sku={sku} onSave={onSave} />);

  fireEvent.change(screen.getByLabelText("商品身份"), { target: { value: "已保存身份锁" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Prompt" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  view.rerender(<PromptEditor sku={sku} onSave={onSave} />);

  expect(screen.getByLabelText("商品身份")).toHaveValue("已保存身份锁");
});

test("adopts the acknowledged SKU snapshot as the new prompt baseline", async () => {
  const onSave = vi.fn().mockResolvedValue({ id: "cluster-1", version: 4 });
  const view = render(<PromptEditor sku={sku} onSave={onSave} />);

  fireEvent.change(screen.getByLabelText("商品身份"), { target: { value: "已保存身份锁" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Prompt" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));

  view.rerender(<PromptEditor sku={{ ...sku, version: 4, identityLock: "服务器确认身份锁" }} onSave={onSave} />);
  expect(screen.getByLabelText("商品身份")).toHaveValue("服务器确认身份锁");

  view.rerender(<PromptEditor sku={{ ...sku, version: 5, identityLock: "后续远端身份锁" }} onSave={onSave} />);
  expect(screen.getByLabelText("商品身份")).toHaveValue("后续远端身份锁");
});
