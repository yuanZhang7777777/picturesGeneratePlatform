import { useEffect, useState } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";

import { PromptEditor } from "./PromptEditor";
import type { ClusterUpdateInput, ProductAsset, ProductSku } from "../types";

const statusText: Record<string, string> = {
  pending: "等待 AI 识别",
  preparing: "AI 正在识别",
  ready: "可生成",
  blocked: "需要确认",
  failed: "需要处理",
};

export function ProductCard({
  sku,
  assets,
  mergeableAssets,
  selected,
  onSelect,
  onMerge,
  onSave,
  onDeleteAsset,
  onDelete,
  disabled,
}: {
  sku: ProductSku;
  assets: ProductAsset[];
  mergeableAssets: ProductAsset[];
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onMerge: (assetId: string) => void;
  onSave: (payload: ClusterUpdateInput) => void;
  onDeleteAsset: (assetId: string) => void;
  onDelete: () => void;
  disabled?: boolean;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: sku.id, disabled });
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [name, setName] = useState(sku.name);
  const [brief, setBrief] = useState(sku.brief);
  const [market, setMarket] = useState(sku.overrides?.market ?? "");
  useEffect(() => { setName(sku.name); setBrief(sku.brief); setMarket(sku.overrides?.market ?? ""); }, [sku.name, sku.brief, sku.overrides?.market]);
  const label = name.trim() || "未命名商品";
  const override = sku.overrides;
  const config = sku.effectiveConfig;
  const status = sku.preparationStatus ?? "pending";
  const saveText = () => {
    const payload: ClusterUpdateInput = {};
    if (name !== sku.name) payload.name = name;
    if (brief !== sku.brief) payload.prompt_override = brief;
    if (Object.keys(payload).length) onSave(payload);
  };

  return <article ref={setNodeRef} className={`surface product-card overflow-hidden ${isOver ? "ring-2 ring-indigo-500" : ""}`}>
    <div className="relative aspect-square bg-slate-100">
      {assets[0]?.imageUrl ? <img className="size-full object-cover" src={assets[0].imageUrl} alt="商品参考图" /> : <span className="grid size-full place-items-center text-sm text-slate-400">等待图片</span>}
      {assets.length > 1 && <span className="absolute bottom-3 left-3 rounded-full bg-slate-950/80 px-2.5 py-1 text-xs font-semibold text-white">共 {assets.length} 张</span>}
      <label className="absolute right-3 top-3 inline-flex items-center gap-1 rounded-full bg-white/90 px-2 py-1 text-xs font-semibold text-slate-700 shadow-sm"><input aria-label={`生成 ${label}`} className="size-4" type="checkbox" checked={selected} onChange={(event) => onSelect(event.target.checked)} />生成</label>
    </div>
    <div className="space-y-3 p-4">
      <input aria-label="商品名称" className="h-9 font-semibold" value={name} placeholder="可不填，AI 将根据图片识别" onChange={(event) => setName(event.target.value)} onBlur={saveText} />
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs font-medium text-slate-500">平台
          <select aria-label="商品平台" className="mt-1" value={override?.platform ?? ""} onChange={(event) => onSave({ platform_override: event.target.value || null })}>
            <option value="">跟随项目</option><option value="shopee">Shopee</option><option value="tiktok">TikTok Shop</option>
          </select>
        </label>
        <label className="text-xs font-medium text-slate-500">国家/站点
          <input aria-label="商品国家" className="mt-1" value={market} placeholder={config?.market || "跟随项目"} onChange={(event) => setMarket(event.target.value.toUpperCase())} onBlur={() => { if (market !== (override?.market ?? "")) onSave({ market_override: market.trim() || null }); }} />
        </label>
      </div>
      <label className="block text-xs font-medium text-slate-500">补充信息
        <textarea aria-label="商品补充信息" className="mt-1 min-h-18" value={brief} placeholder="材质、功能、消费者或特别要求" onChange={(event) => setBrief(event.target.value)} onBlur={saveText} />
      </label>
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className={status === "blocked" ? "font-semibold text-amber-700" : "text-slate-500"}>{statusText[status] ?? status}</span>
        <span className="text-slate-400">{override?.platform || override?.market ? "已单独设置" : "跟随项目"}</span>
      </div>
      {(override?.platform || override?.market || override?.sellerTier) && <button className="text-xs font-semibold text-indigo-700" type="button" onClick={() => onSave({ platform_override: null, market_override: null, seller_tier_override: null })}>恢复跟随项目</button>}
      <div className="flex items-center justify-between gap-2">
        <button className="text-sm font-semibold text-slate-600" type="button" onClick={() => setDetailsOpen(true)}>更多</button>
        <button className="text-sm font-semibold text-rose-700" type="button" disabled={disabled} onClick={() => { if (window.confirm(`删除“${label}”？有历史结果时只会归档。`)) onDelete(); }}>删除</button>
      </div>
    </div>
    {detailsOpen && <div className="fixed inset-0 z-40 bg-slate-950/20" role="presentation" onMouseDown={() => setDetailsOpen(false)}><aside aria-label={`${label} 商品详情`} className="absolute inset-y-0 right-0 w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-2xl" role="dialog" onMouseDown={(event) => event.stopPropagation()}>
      <div className="flex items-center justify-between gap-3"><div><p className="section-label">商品详情与 Prompt</p><h2 className="mt-1 text-xl font-semibold text-slate-950">{label}</h2></div><button className="secondary-button" type="button" onClick={() => setDetailsOpen(false)}>关闭</button></div>
      <div className="mt-5"><p className="text-sm font-semibold text-slate-700">参考图片</p><div className="mt-3 flex flex-wrap gap-3">{assets.map((asset, index) => <DraggableAsset key={asset.id} asset={asset} index={index} disabled={disabled} onDelete={() => onDeleteAsset(asset.id)} />)}</div></div>
      {mergeableAssets.length > 0 && <div className="mt-5"><p className="text-sm font-semibold text-slate-700">添加已有图片</p>{mergeableAssets.map((asset, index) => <button className="mt-2 mr-3 text-sm font-semibold text-indigo-700" disabled={disabled} key={asset.id} onClick={() => onMerge(asset.id)}>合并未分配图片 {index + 1}</button>)}</div>}
      <PromptEditor sku={sku} onSave={onSave} disabled={disabled} />
    </aside></div>}
  </article>;
}

function DraggableAsset({ asset, index, onDelete, disabled }: { asset: ProductAsset; index: number; onDelete: () => void; disabled?: boolean }) {
  const { attributes, listeners, setNodeRef, transform } = useDraggable({ id: asset.id, disabled });
  const style = transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : undefined;
  return <div className="relative size-20 shrink-0"><button ref={setNodeRef} style={style} {...listeners} {...attributes} aria-label={`拖拽商品参考图 ${index + 1}`} className="size-20 overflow-hidden rounded-lg border border-slate-200 bg-slate-100">{asset.imageUrl ? <img className="size-full object-cover" src={asset.imageUrl} alt={`商品参考图 ${index + 1}`} /> : <span className="grid size-full place-items-center text-xs text-slate-400">待预览</span>}</button><button aria-label={`删除商品参考图 ${index + 1}`} className="absolute -right-1 -top-1 grid size-5 place-items-center rounded-full bg-slate-950 text-xs text-white" type="button" disabled={disabled} onClick={(event) => { event.stopPropagation(); if (window.confirm(`删除第 ${index + 1} 张商品参考图？`)) onDelete(); }}>×</button></div>;
}
