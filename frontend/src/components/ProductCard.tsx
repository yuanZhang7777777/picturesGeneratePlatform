import { useEffect, useRef, useState } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";

import { ApiError } from "../api";
import { PromptEditor } from "./PromptEditor";
import type { ClusterUpdateInput, ClusterUpdateResult, ProductAsset, ProductSku } from "../types";

const statusText: Record<string, string> = { pending: "等待 AI 识别", preparing: "AI 正在识别", ready: "可生成", blocked: "需要确认", failed: "需要处理" };
const blockedText: Record<string, string> = { identity_needs_input: "请确认商品身份或补充商品名称", identity_conflict: "图片与商品名称可能不一致，请确认后修改", configuration_required: "请先设置平台和国家", rule_gate_blocked: "请修改与平台规则冲突的商品信息" };
type Draft = { name: string; brief: string; platform: string; market: string; sellerTier: string };

function draftFromSku(sku: ProductSku): Draft {
  return { name: sku.name, brief: sku.brief, platform: sku.overrides?.platform ?? "", market: sku.overrides?.market ?? "", sellerTier: sku.overrides?.sellerTier ?? "" };
}

export function ProductCard({ sku, assets, mergeableAssets, selected, onSelect, onMerge, onSave, onReload, onDeleteAsset, onDelete, disabled }: {
  sku: ProductSku; assets: ProductAsset[]; mergeableAssets: ProductAsset[]; selected: boolean; onSelect: (checked: boolean) => void; onMerge: (assetId: string) => void; onSave: (payload: ClusterUpdateInput, expectedVersion: number) => Promise<ClusterUpdateResult>; onReload: () => Promise<unknown> | void; onDeleteAsset: (assetId: string) => void; onDelete: () => void; disabled?: boolean;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: sku.id, disabled });
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [draft, setDraft] = useState(() => draftFromSku(sku));
  const [savedDraft, setSavedDraft] = useState(() => draftFromSku(sku));
  const [currentVersion, setCurrentVersion] = useState(sku.version);
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const closeButton = useRef<HTMLButtonElement>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(savedDraft);

  useEffect(() => { setCurrentVersion(sku.version); }, [sku.id, sku.version]);
  useEffect(() => {
    if (!dirty) {
      const next = draftFromSku(sku);
      setDraft(next);
      setSavedDraft(next);
    }
  }, [sku.id, sku.name, sku.brief, sku.overrides?.platform, sku.overrides?.market, sku.overrides?.sellerTier, dirty]);
  useEffect(() => { if (detailsOpen) closeButton.current?.focus(); }, [detailsOpen]);

  const label = draft.name.trim() || "未命名商品";
  const status = sku.preparationStatus ?? "pending";
  const blocked = sku.analysisSnapshot?.readiness?.code;
  const effective = sku.effectiveConfig;
  const source = (value: string | null | undefined) => value ? "已单独设置" : "跟随项目";
  const submit = async (next = draft, resetAllOverrides = false) => {
    const payload: ClusterUpdateInput = {};
    if (next.name !== savedDraft.name) payload.name = next.name;
    if (next.brief !== savedDraft.brief) payload.prompt_override = next.brief;
    if (resetAllOverrides || next.platform !== savedDraft.platform) payload.platform_override = next.platform || null;
    if (resetAllOverrides || next.market !== savedDraft.market) payload.market_override = next.market || null;
    if (resetAllOverrides || next.sellerTier !== savedDraft.sellerTier) payload.seller_tier_override = next.sellerTier ? next.sellerTier as "general" | "mall" : null;
    if (!Object.keys(payload).length) return;
    setSaving(true);
    setSaveError("");
    try {
      const result = await onSave(payload, currentVersion);
      setCurrentVersion(result.version);
      setSavedDraft(next);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setSaveError("商品信息已更新，请保留修改后重试");
        void onReload();
      } else {
        setSaveError(error instanceof Error ? error.message : "保存失败，请重试");
      }
    } finally {
      setSaving(false);
    }
  };
  const resetOverrides = () => {
    const next = { ...draft, platform: "", market: "", sellerTier: "" };
    setDraft(next);
    void submit(next, true);
  };
  const savePrompt = async (payload: ClusterUpdateInput) => {
    setSaving(true);
    setSaveError("");
    try {
      const result = await onSave(payload, currentVersion);
      setCurrentVersion(result.version);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setSaveError("商品信息已更新，请保留修改后重试");
        void onReload();
      } else {
        setSaveError(error instanceof Error ? error.message : "保存失败，请重试");
      }
    } finally {
      setSaving(false);
    }
  };

  return <article ref={setNodeRef} className={`surface product-card aspect-square min-w-0 overflow-hidden ${isOver ? "ring-2 ring-indigo-500" : ""}`}>
    <div className="relative h-[58%] bg-slate-100">
      {assets.length > 1 && <span className="absolute inset-x-3 bottom-1 top-3 rounded-xl border border-slate-300 bg-slate-200" />}
      {assets[0]?.imageUrl ? <img className={assets.length > 1 ? "relative h-[calc(100%-0.75rem)] w-[calc(100%-0.75rem)] object-cover" : "relative size-full object-cover"} src={assets[0].imageUrl} alt="商品参考图" /> : <span className="grid size-full place-items-center text-sm text-slate-400">等待图片</span>}
      {assets.length > 1 && <span className="absolute bottom-3 left-3 rounded-full bg-slate-950/80 px-2.5 py-1 text-xs font-semibold text-white">共 {assets.length} 张</span>}
      <label className="absolute right-3 top-3 inline-flex items-center gap-1 rounded-full bg-white/90 px-2 py-1 text-xs font-semibold text-slate-700 shadow-sm"><input aria-label={`生成 ${label}`} className="size-4" type="checkbox" checked={selected} onChange={(event) => onSelect(event.target.checked)} />生成</label>
    </div>
    <div className="flex h-[42%] min-h-0 flex-col gap-1.5 p-3">
      <input aria-label="商品名称" className="h-8 min-h-0 font-semibold" value={draft.name} placeholder="可不填，AI 将根据图片识别" onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
      <p className="truncate text-xs text-slate-500">{effective?.platform || "未设置平台"} · {effective?.market || "未设置国家"} · {effective?.sellerTier === "mall" ? "Mall" : "普通店"}</p>
      <div className="mt-auto flex items-center justify-between gap-2 text-xs"><span className={status === "blocked" ? "font-semibold text-amber-700" : "text-slate-500"}>{status === "blocked" ? blockedText[blocked ?? ""] ?? "请查看商品详情后处理" : statusText[status] ?? status}</span><button className="text-sm font-semibold text-indigo-700" type="button" onClick={() => setDetailsOpen(true)}>更多设置</button></div>
    </div>
    {detailsOpen && <ProductDetails closeButton={closeButton} label={label} sku={sku} assets={assets} mergeableAssets={mergeableAssets} draft={draft} effective={effective} saving={saving || disabled} saveError={saveError} onDraft={setDraft} onSave={() => void submit()} onReset={resetOverrides} onPromptSave={(payload) => void savePrompt(payload)} onMerge={onMerge} onDeleteAsset={onDeleteAsset} onDelete={onDelete} onClose={() => setDetailsOpen(false)} source={source} />}
  </article>;
}

function ProductDetails({ closeButton, label, sku, assets, mergeableAssets, draft, effective, saving, saveError, onDraft, onSave, onReset, onPromptSave, onMerge, onDeleteAsset, onDelete, onClose, source }: { closeButton: React.RefObject<HTMLButtonElement | null>; label: string; sku: ProductSku; assets: ProductAsset[]; mergeableAssets: ProductAsset[]; draft: Draft; effective?: ProductSku["effectiveConfig"]; saving?: boolean; saveError: string; onDraft: (next: Draft) => void; onSave: () => void; onReset: () => void; onPromptSave: (payload: ClusterUpdateInput) => void; onMerge: (assetId: string) => void; onDeleteAsset: (assetId: string) => void; onDelete: () => void; onClose: () => void; source: (value: string | null | undefined) => string }) {
  useEffect(() => { const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; document.addEventListener("keydown", close); return () => document.removeEventListener("keydown", close); }, [onClose]);
  const sellerText = effective?.sellerTier === "mall" ? "Mall" : "普通店";
  return <div className="fixed inset-0 z-40 bg-slate-950/20" role="presentation" onMouseDown={onClose}><aside aria-label={`${label} 商品详情`} aria-modal="true" className="absolute inset-y-0 right-0 w-full max-w-lg overflow-y-auto bg-white p-6 shadow-2xl" role="dialog" onMouseDown={(event) => event.stopPropagation()}>
    <div className="flex items-center justify-between gap-3"><div><p className="section-label">商品详情与 Prompt</p><h2 className="mt-1 text-xl font-semibold text-slate-950">{label}</h2></div><button ref={closeButton} className="secondary-button" type="button" onClick={onClose}>关闭</button></div>
    <div className="mt-5 rounded-xl bg-slate-50 p-3 text-xs text-slate-600"><p>平台：{effective?.platform || "未设置"}（{source(sku.overrides?.platform)}）</p><p className="mt-1">国家：{effective?.market || "未设置"}（{source(sku.overrides?.market)}）</p><p className="mt-1">店铺类型：{sellerText}（{source(sku.overrides?.sellerTier)}）</p></div>
    <label className="mt-5 block text-sm font-medium text-slate-700">平台<select aria-label="商品平台" className="mt-2" value={draft.platform} onChange={(event) => onDraft({ ...draft, platform: event.target.value })}><option value="">跟随项目</option><option value="shopee">Shopee</option><option value="tiktok">TikTok Shop</option></select></label>
    <label className="mt-4 block text-sm font-medium text-slate-700">国家/站点<input aria-label="商品国家" className="mt-2" value={draft.market} placeholder={effective?.market || "跟随项目"} onChange={(event) => onDraft({ ...draft, market: event.target.value.toUpperCase() })} /></label>
    <label className="mt-4 block text-sm font-medium text-slate-700">补充信息<textarea aria-label="商品补充信息" className="mt-2" value={draft.brief} placeholder="材质、功能、消费者或特别要求" onChange={(event) => onDraft({ ...draft, brief: event.target.value })} /></label>
    <label className="mt-4 block text-sm font-medium text-slate-700">店铺类型<select aria-label="商品店铺类型" className="mt-2" value={draft.sellerTier} onChange={(event) => onDraft({ ...draft, sellerTier: event.target.value })}><option value="">跟随项目</option><option value="general">普通店</option><option value="mall">Mall</option></select></label>
    {saveError && <p className="mt-3 text-sm text-amber-700">{saveError}</p>}
    <div className="mt-4 flex flex-wrap gap-2"><button className="primary-button" type="button" disabled={saving} onClick={onSave}>保存修改</button>{(sku.overrides?.platform || sku.overrides?.market || sku.overrides?.sellerTier || draft.platform || draft.market || draft.sellerTier) && <button className="secondary-button" type="button" disabled={saving} onClick={onReset}>恢复跟随项目</button>}</div>
    <div className="mt-5"><p className="text-sm font-semibold text-slate-700">参考图片</p><div className="mt-3 flex flex-wrap gap-3">{assets.map((asset, index) => <DraggableAsset key={asset.id} asset={asset} index={index} disabled={saving} onDelete={() => onDeleteAsset(asset.id)} />)}</div></div>
    {mergeableAssets.length > 0 && <div className="mt-5"><p className="text-sm font-semibold text-slate-700">添加已有图片</p>{mergeableAssets.map((asset, index) => <button className="mt-2 mr-3 text-sm font-semibold text-indigo-700" disabled={saving} key={asset.id} onClick={() => onMerge(asset.id)}>合并未分配图片 {index + 1}</button>)}</div>}
    <div className="mt-6 border-t border-slate-100 pt-5"><PromptEditor sku={sku} onSave={onPromptSave} disabled={saving} /><button className="mt-4 text-sm font-semibold text-rose-700" type="button" disabled={saving} onClick={() => { if (window.confirm(`删除“${label}”？有历史结果时只会归档。`)) onDelete(); }}>删除商品</button></div>
  </aside></div>;
}

function DraggableAsset({ asset, index, onDelete, disabled }: { asset: ProductAsset; index: number; onDelete: () => void; disabled?: boolean }) {
  const { attributes, listeners, setNodeRef, transform } = useDraggable({ id: asset.id, disabled });
  const style = transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : undefined;
  return <div className="relative size-20 shrink-0"><button ref={setNodeRef} style={style} {...listeners} {...attributes} aria-label={`拖拽商品参考图 ${index + 1}`} className="size-20 overflow-hidden rounded-lg border border-slate-200 bg-slate-100">{asset.imageUrl ? <img className="size-full object-cover" src={asset.imageUrl} alt={`商品参考图 ${index + 1}`} /> : <span className="grid size-full place-items-center text-xs text-slate-400">待预览</span>}</button><button aria-label={`删除商品参考图 ${index + 1}`} className="absolute -right-1 -top-1 grid size-5 place-items-center rounded-full bg-slate-950 text-xs text-white" type="button" disabled={disabled} onClick={(event) => { event.stopPropagation(); if (window.confirm(`删除第 ${index + 1} 张商品参考图？`)) onDelete(); }}>×</button></div>;
}
