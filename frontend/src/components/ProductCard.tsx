import { useDraggable, useDroppable } from "@dnd-kit/core";
import { PromptEditor } from "./PromptEditor";
import type { ProductAsset, ProductSku } from "../types";

export function ProductCard({
  sku,
  assets,
  mergeableAssets,
  selected,
  onSelect,
  onMerge,
  onSave,
  disabled,
}: {
  sku: ProductSku;
  assets: ProductAsset[];
  mergeableAssets: ProductAsset[];
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onMerge: (assetId: string) => void;
  onSave: (payload: Record<string, string>) => void;
  disabled?: boolean;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: sku.id, disabled });
  return (
    <article ref={setNodeRef} className={`surface p-4 ${isOver ? "ring-2 ring-indigo-500" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="section-label">商品 / SKU</p>
          <h2 className="mt-1 text-lg font-semibold text-slate-950">{sku.name}</h2>
          <p className="mt-1 text-xs text-slate-500">{sku.sku ? `主 SKU ${sku.sku}` : "上传商品"}</p>
        </div>
        <label className="inline-flex items-center gap-2 text-sm font-medium text-slate-600">
          <input className="size-4" type="checkbox" checked={selected} onChange={(event) => onSelect(event.target.checked)} />
          生成
        </label>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        {assets.map((asset) => <DraggableAsset key={asset.id} asset={asset} disabled={disabled} />)}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {mergeableAssets.map((asset) => (
          <button className="text-sm font-semibold text-indigo-700" disabled={disabled} key={asset.id} onClick={() => onMerge(asset.id)}>
            合并 {asset.name} 到{sku.name}
          </button>
        ))}
      </div>
      <PromptEditor sku={sku} onSave={onSave} disabled={disabled} />
    </article>
  );
}

function DraggableAsset({ asset, disabled }: { asset: ProductAsset; disabled?: boolean }) {
  const { attributes, listeners, setNodeRef, transform } = useDraggable({ id: asset.id, disabled });
  const style = transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : undefined;
  return (
    <button ref={setNodeRef} style={style} {...listeners} {...attributes} className="overflow-hidden rounded-lg border border-slate-200 bg-white text-left shadow-sm">
      {asset.imageUrl ? <img className="aspect-square w-full object-cover" src={asset.imageUrl} alt={asset.name} /> : <div className="grid aspect-square place-items-center bg-slate-100 text-xs text-slate-400">{asset.kind === "txt" ? "TXT" : "待预览"}</div>}
      <span className="block truncate px-2 py-2 text-xs text-slate-600">{asset.name}</span>
    </button>
  );
}
