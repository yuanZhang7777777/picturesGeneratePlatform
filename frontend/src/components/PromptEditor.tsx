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

const riskLabels: Record<string, string> = { low: "低风险", medium: "中风险", high: "高风险" };
const strategyLabels: Record<string, string> = {
  fab_value: "FAB价值",
  scene_ownership: "场景代入",
  emotion: "情绪触发",
  personification: "商品拟人",
  identity_signal: "身份表达",
};

type PromptDraft = {
  identityLock: string;
  prompts: ProductPrompt[];
};

function promptsFromSku(sku: ProductSku) {
  const promptsByOrder = new Map((sku.prompts ?? []).map((prompt) => [prompt.slotOrder, prompt]));
  return defaultPrompts.map((fallback) => ({ ...fallback, ...promptsByOrder.get(fallback.slotOrder) }));
}

function draftFromSku(sku: ProductSku): PromptDraft {
  return {
    identityLock: sku.identityLock,
    prompts: promptsFromSku(sku),
  };
}

function ruleMessage(value: RuleGateMessage) {
  if (typeof value === "string") return value;
  return value.message ?? value.reason ?? value.statement ?? value.rule_id ?? "需人工复核";
}

function promptMeta(prompt: ProductPrompt) {
  const mode = prompt.creativeStrategy?.mode ?? prompt.localizedCopy?.strategyMode;
  const label = mode ? strategyLabels[mode] ?? mode : "";
  const copyLines = prompt.localizedCopy?.lines?.filter(Boolean) ?? [];
  return {
    label,
    copyLines,
    task: prompt.decisionTask ?? prompt.conversionGoal ?? "",
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
  }, [sku.id, sku.version, sku.identityLock, sku.prompts, dirty]);

  const updatePrompt = (slotOrder: number, text: string) => {
    setDraft((current) => ({ ...current, prompts: current.prompts.map((prompt) => prompt.slotOrder === slotOrder ? { ...prompt, text } : prompt) }));
  };
  const save = async () => {
    const next = draft;
    try {
      const result = await onSave({
        identity_lock: next.identityLock,
        prompts: next.prompts
          .filter((prompt) => !prompt.readOnly && prompt.text.trim())
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
  const hardBlocks = gate?.hard_blocks ?? [];
  const semanticRisks = gate?.semantic_risks ?? [];
  const warnings = gate?.warnings ?? [];
  const hasGateSummary = hardBlocks.length + semanticRisks.length + warnings.length > 0;
  const preparation = sku.preparation;
  const preparing = (preparation?.status ?? sku.preparationStatus) === "preparing";
  const progressTotal = preparation?.total || 7;
  const progressCurrent = Math.min(preparation?.current ?? 0, progressTotal);
  const stage = preparation?.stage ?? "";
  const promptStage = ["N4", "N5", "N6", "N7"].includes(stage);
  const progressLabel = promptStage ? "Prompt / 规则生成中" : "商品识别分析中";
  const promptPlaceholder = preparing && !promptStage ? "商品识别和事实分析完成后，会自动生成并加载到这里" : preparing ? "正在生成这个槽位的 Prompt，生成后会自动加载到这里" : "预备生成后显示，可人工微调";

  return (
    <section className="mt-4 space-y-4">
      {ledger && (
        <section className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-800">推断台账</h3>
            {ledger.review_summary && (
              <p className="text-xs text-slate-500">
                确认 {ledger.review_summary.confirmed_count} · 观察 {ledger.review_summary.observed_count} · 推断 {ledger.review_summary.inferred_count} · 高风险 {ledger.review_summary.high_risk_count}
              </p>
            )}
          </div>
          <div className="mt-3 space-y-2">
            {facts.map((fact) => (
              <article className="rounded-md bg-white px-3 py-2" key={fact.fact_id}>
                <p className="text-sm text-slate-800">{fact.statement}</p>
                <p className="mt-1 text-xs text-slate-500">
                  {{ confirmed: "已确认", observed: "已观察", inferred: "合理推断" }[fact.fact_class]} · {Math.round(fact.confidence * 100)}% · {riskLabels[fact.risk_level] ?? "待复核"}
                </p>
                {fact.evidence_refs.length > 0 && <p className="mt-1 text-xs text-slate-400">来源：{fact.evidence_refs.join("、")}</p>}
                {fact.review_note && <p className="mt-1 text-xs text-amber-700">{fact.review_note}</p>}
              </article>
            ))}
          </div>
          {ledger.blocked_claim_topics && ledger.blocked_claim_topics.length > 0 && (
            <p className="mt-3 text-xs text-rose-700">禁止推断进入文案：{ledger.blocked_claim_topics.join("、")}</p>
          )}
        </section>
      )}
      {hasGateSummary && (
        <section className="rounded-lg border border-rose-200 bg-rose-50 p-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-rose-800">规则 / 合规阻断</h3>
            {gate?.decision === "block" && <span className="text-xs font-semibold text-rose-700">已阻断</span>}
          </div>
          {hardBlocks.map((item, index) => <p className="mt-2 text-sm text-rose-800" key={`hard-${index}`}>硬阻断：{ruleMessage(item)}</p>)}
          {semanticRisks.map((item, index) => <p className="mt-2 text-sm text-amber-800" key={`risk-${index}`}>语义风险：{ruleMessage(item)}</p>)}
          {warnings.map((item, index) => <p className="mt-2 text-sm text-slate-600" key={`warning-${index}`}>提示：{ruleMessage(item)}</p>)}
        </section>
      )}
      <label className="block text-sm font-medium text-slate-700">
        <span className="mb-2 block">商品身份</span>
        <textarea aria-label="商品身份" value={draft.identityLock} onChange={(event) => setDraft((current) => ({ ...current, identityLock: event.target.value }))} />
      </label>
      <section className="rounded-lg bg-slate-50 p-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-slate-700">1+8 输出 Prompt</h3>
          {preparing && <span className="text-xs font-semibold text-indigo-700">{progressLabel} {progressCurrent}/{progressTotal}</span>}
        </div>
        {preparing && <ProgressBar current={progressCurrent} total={progressTotal} />}
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {draft.prompts.map((prompt) => {
            const label = `${String(prompt.slotOrder).padStart(2, "0")} ${slotLabel(prompt.slot, prompt.slotOrder)} Prompt`;
            return (
            <label className="block text-sm font-medium text-slate-700" key={prompt.slotOrder}>
              <span className="mb-2 block">{label}</span>
              <PromptMeta prompt={prompt} />
              <textarea
                aria-label={label}
                disabled={prompt.readOnly}
                placeholder={promptPlaceholder}
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
        保存 Prompt
      </button>
    </section>
  );
}

function ProgressBar({ current, total }: { current: number; total: number }) {
  const percent = total ? Math.min(100, Math.round((current / total) * 100)) : 0;
  return <div className="progress-track mt-3" aria-label="预备生成进度" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={current}><span className="progress-fill progress-fill-active" style={{ width: `${percent}%` }} /></div>;
}

function PromptMeta({ prompt }: { prompt: ProductPrompt }) {
  const meta = promptMeta(prompt);
  if (!meta.label && !meta.task && !meta.backTranslation && meta.copyLines.length === 0) return null;
  return (
    <div className="mb-2 rounded-md bg-white px-3 py-2 text-xs text-slate-600">
      <div className="flex flex-wrap gap-2">
        {meta.label && <span className="rounded-full bg-indigo-50 px-2 py-1 font-semibold text-indigo-700">{meta.label}</span>}
        {meta.task && <span>购买任务：{meta.task}</span>}
      </div>
      {meta.copyLines.length > 0 && <p className="mt-1">图片文案：{meta.copyLines.join(" / ")}</p>}
      {meta.backTranslation && <p className="mt-1 text-slate-500">回译：{meta.backTranslation}</p>}
    </div>
  );
}
