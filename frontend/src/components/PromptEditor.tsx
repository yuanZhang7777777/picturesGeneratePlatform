import { useEffect, useState } from "react";
import type { ClusterUpdateInput, ProductPrompt, ProductSku, RelationType, RuleGateMessage } from "../types";

const defaultPrompts = [
  "白底标准图",
  "第二角度/结构图",
  "核心卖点图",
  "材质或细节图",
  "使用场景图",
  "模特或比例展示图",
  "尺寸/包装/包含物图",
  "平台转化营销图",
  "补充转化图",
].map((slot, index) => ({ slotOrder: index + 1, slot, text: "" }));

const riskLabels: Record<string, string> = { low: "低风险", medium: "中风险", high: "高风险" };

function ruleMessage(value: RuleGateMessage) {
  if (typeof value === "string") return value;
  return value.message ?? value.reason ?? value.statement ?? value.rule_id ?? "需人工复核";
}

export function PromptEditor({
  sku,
  onSave,
  disabled,
}: {
  sku: ProductSku;
  onSave: (payload: ClusterUpdateInput) => void;
  disabled?: boolean;
}) {
  const [relationType, setRelationType] = useState<RelationType>(sku.relationType ?? "single_product");
  const [identityLock, setIdentityLock] = useState(sku.identityLock);
  const [brief, setBrief] = useState(sku.brief);
  const [prompts, setPrompts] = useState<ProductPrompt[]>(sku.prompts?.length ? sku.prompts : defaultPrompts);

  useEffect(() => {
    setRelationType(sku.relationType ?? "single_product");
    setIdentityLock(sku.identityLock);
    setBrief(sku.brief);
    setPrompts(sku.prompts?.length ? sku.prompts : defaultPrompts);
  }, [sku]);

  const updatePrompt = (slotOrder: number, text: string) => {
    setPrompts((current) => current.map((prompt) => prompt.slotOrder === slotOrder ? { ...prompt, text } : prompt));
  };
  const ledger = sku.analysisSnapshot?.fact_ledger;
  const gate = sku.analysisSnapshot?.rule_gate;
  const facts = ledger?.facts ?? [];
  const hardBlocks = gate?.hard_blocks ?? [];
  const semanticRisks = gate?.semantic_risks ?? [];
  const warnings = gate?.warnings ?? [];
  const hasGateSummary = hardBlocks.length + semanticRisks.length + warnings.length > 0;

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
                  {fact.fact_class} · {Math.round(fact.confidence * 100)}% · {riskLabels[fact.risk_level] ?? `${fact.risk_level}风险`}
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
        <span className="mb-2 block">多图关系</span>
        <select value={relationType} onChange={(event) => setRelationType(event.target.value as RelationType)}>
          <option value="single_product">一图一商品</option>
          <option value="same_product">同商品参考</option>
          <option value="variant_group">多色/多款组合</option>
        </select>
      </label>
      <label className="block text-sm font-medium text-slate-700">
        <span className="mb-2 block">身份锁</span>
        <textarea value={identityLock} onChange={(event) => setIdentityLock(event.target.value)} />
      </label>
      <label className="block text-sm font-medium text-slate-700">
        <span className="mb-2 block">整套要求</span>
        <textarea value={brief} onChange={(event) => setBrief(event.target.value)} />
      </label>
      <details className="rounded-lg bg-slate-50 p-3">
        <summary className="cursor-pointer text-sm font-semibold text-slate-700">9 槽 Prompt</summary>
        <div className="mt-3 grid gap-3">
          {prompts.map((prompt) => (
            <label className="block text-sm font-medium text-slate-700" key={prompt.slotOrder}>
              <span className="mb-2 block">{String(prompt.slotOrder).padStart(2, "0")} {prompt.slot} Prompt</span>
              <textarea
                disabled={prompt.readOnly}
                value={prompt.text}
                onChange={(event) => updatePrompt(prompt.slotOrder, event.target.value)}
              />
            </label>
          ))}
        </div>
      </details>
      <button
        className="secondary-button"
        disabled={disabled}
        onClick={() => onSave({
          relation_type: relationType,
          identity_lock: identityLock,
          prompt_override: brief,
          prompts: prompts
            .filter((prompt) => !prompt.readOnly && prompt.text.trim())
            .map((prompt) => ({ slot_order: prompt.slotOrder, prompt: prompt.text })),
        })}
      >
        保存 Prompt
      </button>
    </section>
  );
}
