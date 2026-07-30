import { useEffect, useState } from "react";
import type { ProductPrompt, ProductSku, RelationType } from "../types";

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

export function PromptEditor({
  sku,
  onSave,
  disabled,
}: {
  sku: ProductSku;
  onSave: (payload: Record<string, string>) => void;
  disabled?: boolean;
}) {
  const [name, setName] = useState(sku.name);
  const [relationType, setRelationType] = useState<RelationType>(sku.relationType ?? "single_product");
  const [identityLock, setIdentityLock] = useState(sku.identityLock);
  const [brief, setBrief] = useState(sku.brief);
  const [prompts, setPrompts] = useState<ProductPrompt[]>(sku.prompts?.length ? sku.prompts : defaultPrompts);

  useEffect(() => {
    setName(sku.name);
    setRelationType(sku.relationType ?? "single_product");
    setIdentityLock(sku.identityLock);
    setBrief(sku.brief);
    setPrompts(sku.prompts?.length ? sku.prompts : defaultPrompts);
  }, [sku]);

  const updatePrompt = (slotOrder: number, text: string) => {
    setPrompts((current) => current.map((prompt) => prompt.slotOrder === slotOrder ? { ...prompt, text } : prompt));
  };

  return (
    <section className="mt-4 space-y-4">
      <label className="block text-sm font-medium text-slate-700">
        <span className="mb-2 block">商品名称</span>
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
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
              <textarea value={prompt.text} onChange={(event) => updatePrompt(prompt.slotOrder, event.target.value)} />
            </label>
          ))}
        </div>
      </details>
      <button
        className="secondary-button"
        disabled={disabled}
        onClick={() => onSave({
          name,
          relation_type: relationType,
          identity_lock: identityLock,
          prompt_override: brief,
          prompts: JSON.stringify(prompts),
        })}
      >
        保存 Prompt
      </button>
    </section>
  );
}
