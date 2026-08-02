import { useDraggable, useDroppable } from "@dnd-kit/core";
import { useEffect, useState } from "react";

import { ApiError } from "../api";
import { commonMarkets, extraMarkets, marketLabel, platformLabel, platforms, stageLabels } from "../labels";
import type { ClusterUpdateInput, ClusterUpdateResult, ProductAsset, ProductSku, RelationType } from "../types";
import { PromptEditor } from "./PromptEditor";

type Draft = { name: string; productFacts: string; productStyle: string; platform: string; market: string; relationType: RelationType };

function draftFromSku(sku: ProductSku): Draft {
  return {
    name: sku.name,
    productFacts: sku.productFacts ?? sku.facts ?? "",
    productStyle: sku.productStyle ?? sku.brief ?? "",
    platform: sku.overrides?.platform ?? sku.effectiveConfig?.platform ?? "generic",
    market: sku.overrides?.market ?? sku.effectiveConfig?.market ?? "SEA",
    relationType: sku.relationType ?? "single_product",
  };
}

function progressText(sku: ProductSku) {
  const generation = sku.generationProgress;
  const generated = generation?.completed ?? generation?.current ?? 0;
  const generationTotal = generation?.total ?? 0;
  const failed = generation?.failed ?? 0;
  const failureText = failed ? ` · 有 ${failed} 张失败` : "";
  if (generation?.active || (generation?.status && !["idle", "completed", "failed"].includes(generation.status) && generated + failed < generationTotal)) return `出图中 · ${generated}/${generationTotal}${failureText}`;
  if (failed) return `出图已结束 · ${generated}/${generationTotal}${failureText}`;
  if (generationTotal && generated === generationTotal) return `出图完成 · ${generated}/${generationTotal}`;
  const preparation = sku.preparation;
  const status = preparation?.status ?? sku.preparationStatus ?? "pending";
  if (status === "ready") return `预备完成 · ${preparation?.total || 7}/${preparation?.total || 7}`;
  if (status === "preparing") {
    const stage = preparation?.stage ?? "";
    return `预备生成中${stage ? ` · ${stage} ${stageLabels[stage] ?? "处理中"}` : ""} · ${preparation?.current ?? 0}/${preparation?.total ?? 7}`;
  }
  if (status === "blocked") return `预备受阻${preparation?.error ? ` · ${friendlyPreparationError(preparation.error)}` : ""}`;
  if (status === "failed") return `预备失败${preparation?.error ? ` · ${friendlyPreparationError(preparation.error)}` : ""}`;
  return `待预备生成 · ${preparation?.current ?? 0}/${preparation?.total ?? 7}`;
}

function friendlyPreparationError(error: string) {
  if (/image_role|visible product identity|schema|JSON|observed_identity/i.test(error)) return "系统识别异常，请重试预备生成";
  if (/no product|cannot confirm|identity_needs_input|product identity/i.test(error)) return "图片中没有可识别商品，请换图或补充商品信息";
  return error;
}

function progressMeta(sku: ProductSku) {
  const generation = sku.generationProgress;
  const generated = generation?.completed ?? generation?.current ?? 0;
  const generationTotal = generation?.total ?? 0;
  if (generation?.active || (generation?.status && !["idle", "completed", "failed"].includes(generation.status) && generated + (generation.failed ?? 0) < generationTotal)) {
    return { text: progressText(sku), current: generated, total: generationTotal || 9, active: true };
  }
  const preparation = sku.preparation;
  if ((preparation?.status ?? sku.preparationStatus) === "preparing") {
    return { text: progressText(sku), current: preparation?.current ?? 0, total: preparation?.total ?? 7, active: true };
  }
  return { text: progressText(sku), current: 0, total: 0, active: false };
}

export function ProductCard({ sku, assets, selected, expanded = false, onOpen = () => undefined, onClose = () => undefined, onSelect, onSave, onReload, onDeleteAsset, onDelete, disabled }: {
  sku: ProductSku;
  assets: ProductAsset[];
  mergeableAssets?: ProductAsset[];
  selected: boolean;
  expanded?: boolean;
  onOpen?: () => void;
  onClose?: () => void;
  onSelect: (checked: boolean) => void;
  onMerge?: (assetId: string) => void;
  onSave: (payload: ClusterUpdateInput, expectedVersion: number) => Promise<ClusterUpdateResult>;
  onReload: () => Promise<unknown> | void;
  onDeleteAsset: (assetId: string) => void;
  onDelete: () => void;
  disabled?: boolean;
}) {
  const draggable = useDraggable({ id: `cluster:${sku.id}`, data: { type: "cluster", clusterId: sku.id }, disabled });
  const droppable = useDroppable({ id: `cluster:${sku.id}`, data: { type: "cluster", clusterId: sku.id }, disabled });
  const [draft, setDraft] = useState(() => draftFromSku(sku));
  const [savedDraft, setSavedDraft] = useState(() => draftFromSku(sku));
  const [currentVersion, setCurrentVersion] = useState(sku.version);
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(savedDraft);
  const label = draft.name.trim() || "未命名商品";
  const allMarkets = [...commonMarkets, ...extraMarkets];
  const marketOptions = allMarkets.some(([code]) => code === draft.market) ? allMarkets : [[draft.market, marketLabel(draft.market)], ...allMarkets] as [string, string][];
  const nameSourceText = sku.productNameSource === "ai" ? "AI 识别，可修改" : sku.productNameSource === "erp" ? "来自 ERP" : "";
  const progress = progressMeta(sku);

  useEffect(() => { setCurrentVersion(sku.version); }, [sku.id, sku.version]);
  useEffect(() => {
    if (!dirty) {
      const next = draftFromSku(sku);
      setDraft(next);
      setSavedDraft(next);
    }
  }, [sku.id, sku.name, sku.productFacts, sku.facts, sku.productStyle, sku.brief, sku.relationType, sku.overrides?.platform, sku.overrides?.market, sku.effectiveConfig?.platform, sku.effectiveConfig?.market, dirty]);

  const submit = async () => {
    const payload: ClusterUpdateInput = {};
    if (draft.name !== savedDraft.name) payload.name = draft.name;
    if (draft.productFacts !== savedDraft.productFacts) payload.product_facts = draft.productFacts;
    if (draft.productStyle !== savedDraft.productStyle) payload.prompt_override = draft.productStyle;
    if (draft.platform !== savedDraft.platform) payload.platform_override = draft.platform;
    if (draft.market !== savedDraft.market) payload.market_override = draft.market;
    if (draft.relationType !== savedDraft.relationType) payload.relation_type = draft.relationType;
    if (!Object.keys(payload).length) return;
    setSaving(true);
    setSaveError("");
    try {
      const result = await onSave(payload, currentVersion);
      setCurrentVersion(result.version);
      setSavedDraft(draft);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setSaveError("商品信息已更新，请保留修改后重试");
        void onReload();
      } else setSaveError(error instanceof Error ? error.message : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  };
  const savePrompt = async (payload: ClusterUpdateInput) => {
    const result = await onSave(payload, currentVersion);
    setCurrentVersion(result.version);
    return result;
  };
  const style = draggable.transform ? { transform: `translate3d(${draggable.transform.x}px, ${draggable.transform.y}px, 0)` } : undefined;
  const setCardRef = (node: HTMLElement | null) => { draggable.setNodeRef(node); droppable.setNodeRef(node); };

  return <div className="min-w-0" data-expanded-product={expanded ? sku.id : undefined}>
    <article
      ref={setCardRef}
      style={style}
      {...draggable.listeners}
      {...draggable.attributes}
      data-dnd-activator
      role="group"
      aria-label={`${label} 商品卡片（可拖拽合并）`}
      className={`surface product-card min-w-0 overflow-hidden ${droppable.isOver ? "ring-2 ring-indigo-500" : ""}`}
      onClick={(event) => { if (!(event.target as HTMLElement).closest("input,select,textarea,button,summary,a")) onOpen(); }}
    >
      <div className="relative aspect-[4/3] bg-slate-100">
        {assets[0]?.imageUrl ? <img className="relative size-full object-contain" src={assets[0].imageUrl} alt={`${label} 商品参考图`} loading="lazy" decoding="async" /> : <span className="grid size-full place-items-center text-sm text-slate-400">等待图片</span>}
        <span className="absolute bottom-2 left-2 rounded-full bg-slate-950/80 px-2 py-1 text-xs font-semibold text-white">{assets.length} 张</span>
        <label className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-full bg-white/90 px-2 py-1 text-xs font-semibold text-slate-700 shadow-sm"><input aria-label={`选择 ${label}`} className="size-4" type="checkbox" checked={selected} onChange={(event) => onSelect(event.target.checked)} />选择</label>
      </div>
      <div className="flex max-w-full gap-2 overflow-x-auto p-3 pb-2" role="list" aria-label={`${label} 参考图排序`}>
        {assets.map((asset, index) => <DraggableAsset key={asset.id} asset={asset} index={index} disabled={saving || disabled} onDelete={() => onDeleteAsset(asset.id)} />)}
      </div>
      <div className="flex flex-col gap-2 p-3">
        <input aria-label={`商品名称 ${label}`} className="h-9 min-h-9 font-semibold" value={draft.name} placeholder="可不填，预备生成时识别" onChange={(event) => setDraft({ ...draft, name: event.target.value })} onBlur={() => void submit()} />
        {nameSourceText && <p className="text-[11px] text-slate-500">{nameSourceText}</p>}
        <div className="grid grid-cols-2 gap-1.5">
          <select aria-label={`商品平台 ${label}`} className="h-9 min-h-9 py-1 text-xs" value={draft.platform} onChange={(event) => setDraft({ ...draft, platform: event.target.value })} onBlur={() => void submit()}>{platforms.map(([code, text]) => <option key={code} value={code}>{text}</option>)}</select>
          <select aria-label={`商品国家 ${label}`} className="h-9 min-h-9 py-1 text-xs" value={draft.market} onChange={(event) => setDraft({ ...draft, market: event.target.value })} onBlur={() => void submit()}>{marketOptions.map(([code, text]) => <option key={code} value={code}>{text}</option>)}</select>
        </div>
        <textarea aria-label={`创意 Brief ${label}`} className="min-h-16 resize-none py-1.5 text-xs" value={draft.productFacts} placeholder="补充材质、功能或使用要求" onChange={(event) => setDraft({ ...draft, productFacts: event.target.value })} onBlur={() => void submit()} />
        <details className="rounded-lg bg-slate-50 px-2 py-1">
          <summary className="cursor-pointer text-xs font-medium text-slate-600">单品风格（选填）</summary>
          <textarea aria-label={`单品风格 ${label}`} className="mt-1 min-h-12 resize-none py-1.5 text-xs" value={draft.productStyle} onChange={(event) => setDraft({ ...draft, productStyle: event.target.value })} onBlur={() => void submit()} />
        </details>
        {saveError && <p className="text-xs text-amber-700">{saveError}</p>}
        <div className="mt-auto space-y-2 text-xs">
          <div className="flex items-center justify-between gap-2"><span className="truncate text-slate-600">{progress.text}</span><button className="shrink-0 font-semibold text-indigo-700" type="button" onClick={onOpen}>查看 {label} 详情</button></div>
          {progress.active && <ProgressBar current={progress.current} total={progress.total} />}
        </div>
      </div>
    </article>
    {expanded && <section className="surface product-card-expanded-detail fixed inset-y-4 right-4 z-40 w-[min(720px,calc(100vw-2rem))] overflow-y-auto p-5 shadow-2xl" role="dialog" aria-modal="false" aria-label={`${label} 商品详情`}>
      <div className="flex items-center justify-between gap-3"><div><p className="section-label">商品身份、事实与 1+8 Prompt</p><h2 className="mt-1 text-xl font-semibold">{label}</h2><p className="mt-1 text-sm text-slate-500">{platformLabel(draft.platform)} · {marketLabel(draft.market)}</p></div><button className="secondary-button" type="button" onClick={onClose}>收起</button></div>
      <IdentitySummary sku={sku} />
      <PromptEditor sku={sku} onSave={savePrompt} disabled={saving || disabled} />
      <button className="mt-4 text-sm font-semibold text-rose-700" type="button" disabled={saving || disabled} onClick={() => { if (window.confirm(`删除“${label}”？有历史结果时只会归档。`)) onDelete(); }}>删除商品</button>
    </section>}
  </div>;
}

function IdentitySummary({ sku }: { sku: ProductSku }) {
  const identity = sku.identity;
  const profile = identity?.product_profile;
  const lock = identity?.identity_lock;
  const appearances = identity?.target_appearances ?? [];
  if (!identity || (!profile?.category && !profile?.primary_appearance && !lock?.must_not_change?.length && !appearances.length)) return null;
  return <section className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4" role="region" aria-label="商品身份卡"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold text-slate-800">商品身份卡</h3>{typeof identity.confidence === "number" && <span className="text-xs text-slate-500">识别置信度 {Math.round(identity.confidence * 100)}%</span>}</div><dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">{identity.product_name && <div><dt className="text-xs text-slate-500">商品名称</dt><dd>{identity.product_name}</dd></div>}{profile?.category && <div><dt className="text-xs text-slate-500">商品类别</dt><dd>{profile.category}</dd></div>}{profile?.primary_appearance && <div><dt className="text-xs text-slate-500">主要外观</dt><dd>{profile.primary_appearance}</dd></div>}{appearances.length ? <div className="sm:col-span-2"><dt className="text-xs text-slate-500">目标外观</dt><dd>{appearances.map((item) => item.label || item.variant_attributes?.join("/") || item.appearance_id).join("、")}</dd></div> : null}{profile?.shared_structure?.length ? <div><dt className="text-xs text-slate-500">共同结构</dt><dd>{profile.shared_structure.join("、")}</dd></div> : null}{lock?.must_not_change?.length ? <div className="sm:col-span-2"><dt className="text-xs text-slate-500">不可改变</dt><dd>{lock.must_not_change.join("、")}</dd></div> : null}</dl></section>;
}

function DraggableAsset({ asset, index, onDelete, disabled }: { asset: ProductAsset; index: number; onDelete: () => void; disabled?: boolean }) {
  const draggable = useDraggable({ id: `asset:${asset.id}`, data: { type: "asset", assetId: asset.id }, disabled });
  const droppable = useDroppable({ id: `asset-target:${asset.id}`, data: { type: "asset-target", assetId: asset.id }, disabled });
  const style = draggable.transform ? { transform: `translate3d(${draggable.transform.x}px, ${draggable.transform.y}px, 0)` } : undefined;
  const setRef = (node: HTMLElement | null) => { draggable.setNodeRef(node); droppable.setNodeRef(node); };
  return <div className="relative size-12 shrink-0" role="listitem"><button ref={setRef} style={style} {...draggable.listeners} {...draggable.attributes} data-dnd-activator aria-label={`拖拽商品参考图 ${index + 1}`} className={`size-12 overflow-hidden rounded-lg border bg-slate-100 ${droppable.isOver ? "border-indigo-500 ring-2 ring-indigo-200" : "border-slate-200"}`}>{asset.imageUrl ? <img className="size-full object-contain" src={asset.imageUrl} alt={`商品参考图 ${index + 1}`} loading="lazy" decoding="async" /> : <span className="grid size-full place-items-center text-xs text-slate-400">待预览</span>}</button>{index === 0 && <span className="pointer-events-none absolute bottom-0.5 left-0.5 rounded bg-slate-950/80 px-1 text-[10px] text-white">主</span>}<button aria-label={`删除商品参考图 ${index + 1}`} className="absolute -right-1 -top-1 grid size-5 place-items-center rounded-full bg-slate-950 text-xs text-white" type="button" disabled={disabled} onClick={() => { if (window.confirm(`删除第 ${index + 1} 张商品参考图？`)) onDelete(); }}>×</button></div>;
}

function ProgressBar({ current, total }: { current: number; total: number }) {
  const safeTotal = total || 1;
  const percent = Math.min(100, Math.round((current / safeTotal) * 100));
  return <div className="progress-track" aria-label="预备生成进度" role="progressbar" aria-valuemin={0} aria-valuemax={safeTotal} aria-valuenow={Math.min(current, safeTotal)}><span className="progress-fill progress-fill-active" style={{ width: `${percent}%` }} /></div>;
}
