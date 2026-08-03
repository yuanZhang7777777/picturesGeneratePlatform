import { useEffect, useRef, useState } from "react";
import { slotLabel } from "../labels";
import type { ClusterUpdateInput, ClusterUpdateResult, ProductPrompt, ProductSku, RuleGateMessage } from "../types";

const defaultPrompts = [
  "标准白底产品图",
  "核心卖点图",
  "商品细节图",
  "功能说明图",
  "使用场景图",
  "模特/比例图",
  "尺寸/包装/包含物图",
  "平台转化营销图",
  "补充转化图",
].map((slot, index) => ({ slotOrder: index + 1, slot, text: "" }));

type PromptDraft = {
  prompts: ProductPrompt[];
};

function visiblePromptText(prompt: ProductPrompt) {
  const hasGeneratedContent = Boolean(
    prompt.text.trim()
    || prompt.displayPrompt?.trim()
    || prompt.decisionTask
    || prompt.conversionGoal
    || prompt.creativeStrategy
    || prompt.localizedCopy?.lines?.length,
  );
  if (!hasGeneratedContent) return "";
  const displayPrompt = prompt.displayPrompt?.trim() ?? "";
  if (displayPrompt && !isInternalImagePrompt(displayPrompt) && !isEnglishHeavy(displayPrompt)) return cleanOperatorPrompt(displayPrompt, prompt);
  if (/[\u3400-\u9fff]/.test(prompt.text) && !isInternalImagePrompt(prompt.text) && !isEnglishHeavy(prompt.text)) return cleanOperatorPrompt(prompt.text, prompt);
  return buildVisualPrompt(prompt, "");
}

function isInternalImagePrompt(value: string) {
  return /Create a|Only render|Scene:|Composition:|Reference|Product facts|buyer motivation|product category|visible text|frame|prompt instructions/i.test(value);
}

function isEnglishHeavy(value: string) {
  const english = (value.match(/[A-Za-z]/g) ?? []).length;
  const chinese = (value.match(/[\u3400-\u9fff]/g) ?? []).length;
  return english > Math.max(18, chinese * 2);
}

function fallbackChinesePrompt(order: number) {
  return [
    "突出完整商品，干净白底，方便上架。",
    "把最容易打动买家的核心好处讲清楚。",
    "展示商品细节、质感或结构，让买家相信品质。",
    "用真实动作说明商品怎么用、解决什么问题。",
    "设计一个能让买家代入的使用场景。",
    "展示商品大小、使用对象或空间比例。",
    "说明尺寸、包装或包含物；没有包装资料时只展示商品本身。",
    "做一张适合平台列表转化的营销图。",
    "补充最后一个购买理由，让买家愿意下单。",
  ][order - 1] ?? `第 ${order} 张图的中文画面策划。`;
}

function extractChinesePart(value: string, label: string) {
  const match = value.match(new RegExp(`${label}[：:]\\s*([^。；;\\n]+)`));
  return match?.[1]?.trim() ?? "";
}

function slotScene(order: number) {
  return [
    "干净白底产品主图，完整呈现商品主体，方便上架。",
    "围绕商品最能打动买家的好处设计一张转化图，商品是视觉中心。",
    "用近景或局部构图展示材质、结构、触感或关键细节。",
    "用一个真实动作说明商品怎么用、解决什么问题。",
    "设计一个买家能代入的使用场景，让商品自然参与其中。",
    "展示商品大小、使用对象或空间比例，让买家快速理解尺度。",
    "展示尺寸、套装、包装或包含物；没有包装资料时只展示商品本身。",
    "做一张适合平台列表点击的营销图，画面有明确购买理由。",
    "补充最后一个转化理由，让买家觉得现在就可以下单。",
  ][order - 1] ?? fallbackChinesePrompt(order);
}

function cleanOperatorPrompt(value: string, prompt: ProductPrompt) {
  if (!/(购买任务|转化目标|用户价值|场景代入|情绪触发|画面[：:]|动作[：:]|构图[：:])/.test(value)) return value;
  return buildVisualPrompt(prompt, value);
}

function buildVisualPrompt(prompt: ProductPrompt, source: string) {
  const scene = extractChinesePart(source, "画面") || slotScene(prompt.slotOrder);
  const action = extractChinesePart(source, "动作") || prompt.creativeStrategy?.mentalSimulation || prompt.creativeStrategy?.consumerBenefit || prompt.decisionTask || "围绕当前图类型安排一个清楚、自然、能看懂的动作。";
  const composition = extractChinesePart(source, "构图") || "商品占据主要视觉位置，背景、道具和人物只服务这张图的购买理由，不照搬上传原图构图。";
  const copy = prompt.localizedCopy?.lines?.filter(Boolean).join(" / ");
  return [
    `画面：${scene}`,
    "主体：商品清晰可见，作为画面主角；多图商品按当前槽位需要选择完整套装或合理子集。",
    `动作：${action}`,
    `构图：${composition}`,
    `图片文案：${copy || "无；如需要文字，预备生成会按所选国家语言生成。"}`
  ].join("\n");
}

function promptsFromSku(sku: ProductSku) {
  const promptsByOrder = new Map((sku.prompts ?? []).map((prompt) => [prompt.slotOrder, prompt]));
  return defaultPrompts.map((fallback) => ({ ...fallback, ...promptsByOrder.get(fallback.slotOrder) }));
}

function draftFromSku(sku: ProductSku): PromptDraft {
  return {
    prompts: promptsFromSku(sku).map((prompt) => ({ ...prompt, text: visiblePromptText(prompt) })),
  };
}

function ruleMessage(value: RuleGateMessage) {
  const raw = typeof value === "string" ? value : value.message ?? value.reason ?? value.statement ?? value.rule_id ?? "需人工复核";
  const labels: Record<string, string> = {
    "copy.high_risk_claim": "图片文案包含医疗、认证、价格折扣或 100% 承诺，生成后请人工确认",
    "semantic_n7_review_warning": "系统建议人工复核，但不会阻止出图",
    "copy.literal_lock": "图片文案可能没有逐字匹配，生成后请重点审核文字",
    "copy.unknown_fact_ref": "图片文案引用了未确认信息，生成后请重点审核",
    "prompt.visible_text_max_three_lines": "图片文案超过 3 行，生成后请重点审核",
    "hero.no_added_text": "白底图建议不加文字，生成后请重点审核",
  };
  if (raw.startsWith("n7.soft_block:")) return `系统建议人工复核：${labels[raw.slice("n7.soft_block:".length)] ?? raw.slice("n7.soft_block:".length)}`;
  if (/no_added_text|no_digital_rendering/i.test(raw)) return "平台规则提示：生成后请人工确认是否适合上架";
  if (/image_role|visible product identity|observed_identity|N2|evidence_refs|fact_refs|reference_plan|schema|JSON|must be/i.test(raw)) return "系统结构化识别提示：已继续出图，结果需人工复核";
  if (/price|discount/i.test(raw)) return "价格/折扣";
  if (/certification|certified/i.test(raw)) return "认证/奖项";
  if (/medical|efficacy|cure/i.test(raw)) return "医疗/疗效";
  if (/100/.test(raw)) return "100% 或绝对承诺";
  if (labels[raw]) return labels[raw];
  if (typeof value === "string") return raw;
  return value.message ?? value.reason ?? value.statement ?? value.rule_id ?? "需人工复核";
}

function quietGateMessage(value: RuleGateMessage) {
  const raw = typeof value === "string" ? value : value.message ?? value.reason ?? value.statement ?? value.rule_id ?? "";
  const combined = `${raw} ${ruleMessage(value)}`;
  return /copy\.high_risk_claim|copy\.literal_lock|semantic_n7_review_warning|price|certification|medical|discount|high.?risk|结构化识别|人工复核|复核|高风险|未确认|缺少确认|推断|消费者文案|发布前|价格|折扣|认证|奖项|医疗|疗效|材质/i.test(combined);
}

function evidenceLabel(value: string) {
  if (value.startsWith("asset:")) return "上传图片";
  if (value.startsWith("observation:")) return "图片识别";
  if (value.startsWith("erp:")) return "ERP资料";
  return value;
}

function promptMeta(prompt: ProductPrompt) {
  const copyLines = prompt.localizedCopy?.lines?.filter(Boolean) ?? [];
  return {
    copyLines,
    backTranslation: prompt.localizedCopy?.backTranslation ?? "",
  };
}

export function PromptEditor({
  sku,
  onSave,
  disabled,
}: {
  sku: ProductSku;
  onSave: (payload: ClusterUpdateInput) => Promise<ClusterUpdateResult> | void;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState(() => draftFromSku(sku));
  const [savedDraft, setSavedDraft] = useState(() => draftFromSku(sku));
  const currentSkuId = useRef(sku.id);
  const pendingVersion = useRef<number | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(savedDraft);

  useEffect(() => {
    const next = draftFromSku(sku);
    const skuChanged = currentSkuId.current !== sku.id;
    currentSkuId.current = sku.id;
    if (skuChanged) {
      pendingVersion.current = null;
      setDraft(next);
      setSavedDraft(next);
      return;
    }
    if (pendingVersion.current !== null) {
      if (sku.version < pendingVersion.current) return;
      pendingVersion.current = null;
    }
    if (!dirty) {
      setDraft(next);
      setSavedDraft(next);
    }
  }, [sku.id, sku.version, sku.prompts, dirty]);

  const updatePrompt = (slotOrder: number, text: string) => {
    setDraft((current) => ({ ...current, prompts: current.prompts.map((prompt) => prompt.slotOrder === slotOrder ? { ...prompt, text } : prompt) }));
  };
  const save = async () => {
    const next = draft;
    try {
      const result = await onSave({
        prompts: next.prompts
          .filter((prompt) => !prompt.readOnly && prompt.text.trim() && prompt.text !== savedDraft.prompts.find((item) => item.slotOrder === prompt.slotOrder)?.text)
          .map((prompt) => ({ slot_order: prompt.slotOrder, prompt: prompt.text })),
      });
      pendingVersion.current = result?.version ?? null;
      setSavedDraft(next);
    } catch {
      // ProductCard shows the save error and keeps this local draft dirty for retry.
    }
  };
  const ledger = sku.analysisSnapshot?.fact_ledger;
  const gate = sku.analysisSnapshot?.rule_gate;
  const facts = ledger?.facts ?? [];
  const hardBlocks = (gate?.hard_blocks ?? []).filter((item) => !quietGateMessage(item));
  const semanticRisks = (gate?.semantic_risks ?? []).filter((item) => !quietGateMessage(item));
  const warnings = (gate?.warnings ?? []).filter((item) => !quietGateMessage(item));
  const hasGateSummary = hardBlocks.length + semanticRisks.length + warnings.length > 0;
  const preparation = sku.preparation;
  const preparing = ["pending", "preparing"].includes(preparation?.status ?? sku.preparationStatus ?? "");
  const progressTotal = preparation?.total || 7;
  const progressCurrent = Math.min(preparation?.current ?? 0, progressTotal);
  const stage = preparation?.stage ?? "";
  const promptStage = ["N4", "N5", "N6", "N7"].includes(stage);
  const progressLabel = promptStage ? "正在生成 1+8 提示词" : "正在读取并理解商品图片";
  const promptPlaceholder = (slotOrder: number) => preparing && !promptStage
    ? "商品图片读取完成后，会自动生成中文提示词"
    : preparing
      ? "正在生成这个槽位的中文提示词"
      : `预备生成后显示：${fallbackChinesePrompt(slotOrder)}`;

  return (
    <section className="mt-4 space-y-4">
      {ledger && (
        <section className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-800">商品识别信息</h3>
            {ledger.review_summary && (
              <p className="text-xs text-slate-500">
                确认 {ledger.review_summary.confirmed_count} · 图片观察 {ledger.review_summary.observed_count} · 辅助判断 {ledger.review_summary.inferred_count}
              </p>
            )}
          </div>
          <div className="mt-3 space-y-2">
            {facts.map((fact) => (
              <article className="rounded-md bg-white px-3 py-2" key={fact.fact_id}>
                <p className="text-sm text-slate-800">{fact.statement}</p>
                <p className="mt-1 text-xs text-slate-500">
                  {{ confirmed: "已确认", observed: "图片观察", inferred: "辅助判断" }[fact.fact_class]} · {Math.round(fact.confidence * 100)}%
                </p>
                {fact.evidence_refs.length > 0 && <p className="mt-1 text-xs text-slate-400">来源：{Array.from(new Set(fact.evidence_refs.map(evidenceLabel))).join("、")}</p>}
                {fact.review_note && !/结构化|异常|price|certification|medical/i.test(fact.review_note) && <p className="mt-1 text-xs text-amber-700">{fact.review_note}</p>}
              </article>
            ))}
          </div>
        </section>
      )}
      {hasGateSummary && (
        <section className="rounded-lg border border-rose-200 bg-rose-50 p-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-rose-800">{hardBlocks.length ? "规则 / 合规阻断" : "人工复核提示"}</h3>
            {gate?.decision === "block" && <span className="text-xs font-semibold text-rose-700">已阻断</span>}
          </div>
          {hardBlocks.map((item, index) => <p className="mt-2 text-sm text-rose-800" key={`hard-${index}`}>硬阻断：{ruleMessage(item)}</p>)}
          {semanticRisks.map((item, index) => <p className="mt-2 text-sm text-amber-800" key={`risk-${index}`}>语义风险：{ruleMessage(item)}</p>)}
          {warnings.map((item, index) => <p className="mt-2 text-sm text-slate-600" key={`warning-${index}`}>提示：{ruleMessage(item)}</p>)}
        </section>
      )}
      <section className="rounded-lg bg-slate-50 p-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-slate-700">1+8 输出提示词</h3>
          {preparing && <span className="text-xs font-semibold text-indigo-700">{progressLabel} {progressCurrent}/{progressTotal}</span>}
        </div>
        {preparing && <ProgressBar current={progressCurrent} total={progressTotal} />}
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {draft.prompts.map((prompt) => {
            const label = `${String(prompt.slotOrder).padStart(2, "0")} ${slotLabel(prompt.slot, prompt.slotOrder)}提示词`;
            return (
            <label className="block text-sm font-medium text-slate-700" key={prompt.slotOrder}>
              <span className="mb-2 block">{label}</span>
              <PromptMeta prompt={prompt} />
              <textarea
                aria-label={label}
                disabled={prompt.readOnly}
                placeholder={promptPlaceholder(prompt.slotOrder)}
                value={prompt.text}
                onChange={(event) => updatePrompt(prompt.slotOrder, event.target.value)}
              />
            </label>
          );
          })}
        </div>
      </section>
      <button
        className="secondary-button"
        disabled={disabled}
        onClick={() => void save()}
      >
        保存提示词
      </button>
    </section>
  );
}

function ProgressBar({ current, total }: { current: number; total: number }) {
  const percent = total ? Math.max(12, Math.min(100, Math.round((current / total) * 100))) : 12;
  return <div className="progress-track mt-3" aria-label="预备生成进度" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={current}><span className="progress-fill progress-fill-active" style={{ width: `${percent}%` }} /></div>;
}

function PromptMeta({ prompt }: { prompt: ProductPrompt }) {
  const meta = promptMeta(prompt);
  if (!meta.backTranslation && meta.copyLines.length === 0) return null;
  return (
    <div className="mb-2 rounded-md bg-white px-3 py-2 text-xs text-slate-600">
      {meta.copyLines.length > 0 && <p className="mt-1">图片文案：{meta.copyLines.join(" / ")}</p>}
      {meta.backTranslation && <p className="mt-1 text-slate-500">回译：{meta.backTranslation}</p>}
    </div>
  );
}
