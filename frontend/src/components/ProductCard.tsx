import { useEffect, useState } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { PromptEditor } from "./PromptEditor";
import type { ClusterUpdateInput, ProductAsset, ProductSku } from "../types";

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
  useEffect(() => setName(sku.name), [sku.name]);
  const label = name.trim() || "未命名商品";

  return (
    <article ref={setNodeRef} className={`surface px-4 py-3 ${isOver ? "ring-2 ring-indigo-500" : ""}`}>
      <div className="flex items-center gap-3">
        <div className="flex min-w-20 max-w-44 gap-2 overflow-x-auto">
          {assets.map((asset, index) => (
            <DraggableAsset
              key={asset.id}
              asset={asset}
              index={index}
              disabled={disabled}
              onDelete={() => onDeleteAsset(asset.id)}
            />
          ))}
        </div>
        <div className="min-w-0 flex-1">
          <input
            aria-label="商品名称"
            className="h-9 font-semibold"
            value={name}
            placeholder="可不填，AI 将根据图片识别"
            onChange={(event) => setName(event.target.value)}
            onBlur={() => {
              if (name !== sku.name) onSave({ name });
            }}
          />
          <p className="mt-1 text-xs text-slate-500">
            {sku.sku ? `SKU ${sku.sku} · ` : ""}{assets.length} 张参考图 · {sku.preparationStatus ?? "待整理"}
          </p>
        </div>
        <label className="inline-flex shrink-0 items-center gap-2 text-sm font-medium text-slate-600">
          <input
            aria-label={`生成 ${label}`}
            className="size-4"
            type="checkbox"
            checked={selected}
            onChange={(event) => onSelect(event.target.checked)}
          />
          生成
        </label>
        <button className="secondary-button shrink-0" type="button" onClick={() => setDetailsOpen(true)}>
          编辑商品详情
        </button>
        <button
          className="shrink-0 text-sm font-semibold text-rose-700"
          type="button"
          disabled={disabled}
          onClick={() => {
            if (window.confirm(`删除“${label}”？有历史结果时只会归档。`)) onDelete();
          }}
        >
          删除商品
        </button>
      </div>
      {mergeableAssets.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-3 border-t border-slate-100 pt-2">
          {mergeableAssets.map((asset, index) => (
            <button
              className="text-xs font-semibold text-indigo-700"
              disabled={disabled}
              key={asset.id}
              onClick={() => onMerge(asset.id)}
            >
              合并未分配图片 {index + 1} 到{label}
            </button>
          ))}
        </div>
      )}
      {detailsOpen && (
        <div className="fixed inset-0 z-40 bg-slate-950/20" role="presentation" onMouseDown={() => setDetailsOpen(false)}>
          <aside
            aria-label={`${label} 商品详情`}
            className="absolute inset-y-0 right-0 w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-2xl"
            role="dialog"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="section-label">商品详情与 Prompt</p>
                <h2 className="mt-1 text-xl font-semibold text-slate-950">{label}</h2>
              </div>
              <button className="secondary-button" type="button" onClick={() => setDetailsOpen(false)}>关闭</button>
            </div>
            <PromptEditor sku={sku} onSave={onSave} disabled={disabled} />
          </aside>
        </div>
      )}
    </article>
  );
}

function DraggableAsset({
  asset,
  index,
  onDelete,
  disabled,
}: {
  asset: ProductAsset;
  index: number;
  onDelete: () => void;
  disabled?: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform } = useDraggable({ id: asset.id, disabled });
  const style = transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : undefined;
  return (
    <div className="relative size-16 shrink-0">
      <button
        ref={setNodeRef}
        style={style}
        {...listeners}
        {...attributes}
        aria-label={`拖拽商品参考图 ${index + 1}`}
        className="size-16 overflow-hidden rounded-lg border border-slate-200 bg-slate-100"
      >
        {asset.imageUrl
          ? <img className="size-full object-cover" src={asset.imageUrl} alt={`商品参考图 ${index + 1}`} />
          : <span className="grid size-full place-items-center text-xs text-slate-400">待预览</span>}
      </button>
      <button
        aria-label={`删除商品参考图 ${index + 1}`}
        className="absolute -right-1 -top-1 grid size-5 place-items-center rounded-full bg-slate-950 text-xs text-white"
        type="button"
        disabled={disabled}
        onClick={(event) => {
          event.stopPropagation();
          if (window.confirm(`删除第 ${index + 1} 张商品参考图？`)) onDelete();
        }}
      >
        ×
      </button>
    </div>
  );
}
