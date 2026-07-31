import { DndContext, KeyboardSensor, PointerSensor, useDroppable, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, deleteAsset, deleteCluster, generateProject, importSkus, mergeAsset, prepareProject, splitAsset, updateCluster, updateProjectSettings, uploadAssets, type UploadResult } from "../api";
import { ImportPanel } from "../components/ImportPanel";
import { ProductCard } from "../components/ProductCard";
import { commonMarkets, extraMarkets, marketValue, platforms } from "../labels";
import { EmptyState, ErrorPanel, Shell } from "../layout";
import { useProjectSnapshot } from "../queries";
import type { ClusterUpdateInput, ImportMode, ProductConfiguration } from "../types";

const slotOrders = [1, 2, 3, 4, 5, 6, 7, 8, 9];

function isGlobalError(error: unknown) {
  return !(error instanceof ApiError) || error.authRequired || error.status === 401 || error.status === 403 || error.status >= 500;
}

export default function ProjectGrouping() {
  const { projectId } = useParams();
  const projectQuery = useProjectSnapshot(projectId);
  const queryClient = useQueryClient();
  const [deselectedIds, setDeselectedIds] = useState<Set<string>>(new Set());
  const [importOpen, setImportOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const project = projectQuery.data;
  const selectedClusters = useMemo(() => project?.skus.filter((sku) => !deselectedIds.has(sku.id)) ?? [], [project, deselectedIds]);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor));
  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    await queryClient.invalidateQueries({ queryKey: ["workspace"] });
  };
  const upload = useMutation({ mutationFn: ({ files, mode }: { files: File[]; mode: ImportMode }) => uploadAssets(projectId!, files, mode), onSuccess: async (result) => { setUploadResult(result); await invalidate(); } });
  const skuImport = useMutation({ mutationFn: ({ skus, mode }: { skus: string[]; mode: ImportMode }) => importSkus(projectId!, skus, mode), onSuccess: invalidate });
  const prepare = useMutation({ mutationFn: () => prepareProject(projectId!, selectedClusters.map((sku) => sku.id)), onSuccess: invalidate });
  const generate = useMutation({ mutationFn: () => generateProject(projectId!, { clusterIds: selectedClusters.map((sku) => sku.id), slotOrders }), onSuccess: invalidate });
  const save = useMutation({ mutationFn: ({ skuId, expectedVersion, payload }: { skuId: string; expectedVersion: number; payload: ClusterUpdateInput }) => updateCluster(skuId, expectedVersion, payload), onSuccess: invalidate });
  const removeAsset = useMutation({ mutationFn: deleteAsset, onSuccess: invalidate });
  const removeCluster = useMutation({ mutationFn: deleteCluster, onSuccess: invalidate });
  const saveSettings = useMutation({ mutationFn: (input: ProductConfiguration) => updateProjectSettings(projectId!, input), onSuccess: invalidate });
  const reorganize = useMutation({
    mutationFn: async ({ activeId, overId }: { activeId: string; overId: string }) => {
      if (activeId.startsWith("asset:") && overId === "blank-grid") return splitAsset(activeId.slice(6));
      if (!overId.startsWith("cluster:")) return;
      const target = project!.skus.find((sku) => sku.id === overId.slice(8));
      if (!target) return;
      if (activeId.startsWith("asset:")) {
        const assetId = activeId.slice(6);
        if (target.assetIds.includes(assetId)) return;
        return mergeAsset(target.id, assetId, target.version);
      }
      if (!activeId.startsWith("cluster:")) return;
      const source = project!.skus.find((sku) => sku.id === activeId.slice(8));
      if (!source || source.id === target.id) return;
      if (source.assetIds.length + target.assetIds.length > 16) throw new ApiError(400, "合并后最多保留 16 张商品参考图");
      let version = target.version;
      for (const assetId of source.assetIds) {
        await mergeAsset(target.id, assetId, version);
        version += 1;
      }
    },
    onSuccess: invalidate,
  });

  useEffect(() => {
    if (!expandedId) return;
    const consumeOutsideClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (target.closest(`[data-expanded-product="${expandedId}"]`)) return;
      event.preventDefault();
      event.stopPropagation();
      setExpandedId(null);
    };
    document.addEventListener("click", consumeOutsideClick, true);
    return () => document.removeEventListener("click", consumeOutsideClick, true);
  }, [expandedId]);

  if (projectQuery.isLoading) return <Shell><p className="text-sm text-slate-500">正在读取项目…</p></Shell>;
  if (projectQuery.isError || !project) return <Shell><ErrorPanel error={projectQuery.error ?? new Error("项目快照为空")} retry={() => void projectQuery.refetch()} /></Shell>;

  const onDragEnd = (event: DragEndEvent) => {
    if (event.over) reorganize.mutate({ activeId: String(event.active.id), overId: String(event.over.id) });
  };
  const errors = [upload.error, skuImport.error, prepare.error, generate.error, reorganize.error, save.error, removeAsset.error, removeCluster.error, saveSettings.error].filter(Boolean);
  const localError = errors.find((error) => !isGlobalError(error));
  const globalError = errors.find(isGlobalError);
  const busy = upload.isPending || skuImport.isPending || prepare.isPending || generate.isPending || reorganize.isPending || save.isPending || removeAsset.isPending || removeCluster.isPending;

  return <Shell>
    <ProjectToolbar project={project} selectedCount={selectedClusters.length} pending={saveSettings.isPending} onSave={(input) => saveSettings.mutateAsync(input)} onAdd={() => setImportOpen(true)} onSelectAll={() => setDeselectedIds(new Set())} onDeselectAll={() => setDeselectedIds(new Set(project.skus.map((sku) => sku.id)))} onInvert={() => setDeselectedIds(new Set(project.skus.filter((sku) => !deselectedIds.has(sku.id)).map((sku) => sku.id)))} onPrepare={() => prepare.mutate()} onGenerate={() => generate.mutate()} />
    {importOpen && <ImportModal onClose={() => setImportOpen(false)}><ImportPanel disabled={busy} onUpload={(files, mode) => upload.mutateAsync({ files, mode })} onSkuImport={(skus, mode) => skuImport.mutateAsync({ skus, mode })} onImported={() => setImportOpen(false)} /></ImportModal>}
    {uploadResult && <div className="mb-5 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm">成功导入 {uploadResult.asset_count} 个素材{uploadResult.rejected.length ? `，${uploadResult.rejected.length} 个未导入` : ""}。</div>}
    {localError instanceof ApiError && <p className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{localError.message}</p>}
    {globalError && <div className="mb-5"><ErrorPanel error={globalError} /></div>}
    <DndContext sensors={sensors} onDragEnd={onDragEnd}><ProductGrid>{project.skus.map((sku) => {
      const assets = sku.assets ?? project.assets.filter((asset) => sku.assetIds.includes(asset.id));
      return <ProductCard key={sku.id} sku={sku} assets={assets} selected={!deselectedIds.has(sku.id)} expanded={expandedId === sku.id} disabled={busy} onOpen={() => setExpandedId(sku.id)} onClose={() => setExpandedId(null)} onSave={(payload, expectedVersion) => save.mutateAsync({ skuId: sku.id, expectedVersion, payload })} onReload={() => projectQuery.refetch()} onDeleteAsset={(assetId) => removeAsset.mutate(assetId)} onDelete={() => removeCluster.mutate(sku.id)} onSelect={(next) => setDeselectedIds((current) => { const copy = new Set(current); if (next) copy.delete(sku.id); else copy.add(sku.id); return copy; })} />;
    })}</ProductGrid></DndContext>
    {!project.skus.length && <EmptyState title="还没有商品素材" description="点击“添加商品”上传图片、文件夹或导入 ERP SKU。" />}
  </Shell>;
}

function ProductGrid({ children }: { children: ReactNode }) {
  const blank = useDroppable({ id: "blank-grid", data: { type: "blank" } });
  return <section ref={blank.setNodeRef} className={`product-card-grid min-h-56 rounded-2xl ${blank.isOver ? "bg-indigo-50" : ""}`} aria-label="商品分组网格">{children}</section>;
}

function ProjectToolbar({ project, selectedCount, pending, onSave, onAdd, onSelectAll, onDeselectAll, onInvert, onPrepare, onGenerate }: { project: { id: string; name: string; defaultConfig?: ProductConfiguration; platform: string; market: string; size: string; resolution?: string }; selectedCount: number; pending: boolean; onSave: (input: ProductConfiguration) => Promise<unknown>; onAdd: () => void; onSelectAll: () => void; onDeselectAll: () => void; onInvert: () => void; onPrepare: () => void; onGenerate: () => void }) {
  const initial = () => ({
    platform: project.defaultConfig?.platform || project.platform?.toLowerCase() || "generic",
    market: project.defaultConfig?.market || project.market || "SEA",
    sellerTier: project.defaultConfig?.sellerTier ?? "general" as const,
    size: project.defaultConfig?.size || project.size || "1:1",
    resolution: (project.defaultConfig?.resolution || project.resolution || "1k").toLowerCase(),
    globalPrompt: project.defaultConfig?.globalPrompt || "",
  });
  const [draft, setDraft] = useState<ProductConfiguration>(initial);
  const [saved, setSaved] = useState<ProductConfiguration>(initial);
  const [moreOpen, setMoreOpen] = useState(false);
  const [search, setSearch] = useState("");
  const pendingSaved = useRef<string | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  useEffect(() => {
    const next = initial();
    const serialized = JSON.stringify(next);
    if (pendingSaved.current && pendingSaved.current !== serialized) return;
    if (pendingSaved.current === serialized) pendingSaved.current = null;
    if (!dirty) { setDraft(next); setSaved(next); }
  }, [project.defaultConfig, project.platform, project.market, project.size, project.resolution, dirty]);
  const save = async (next: ProductConfiguration) => {
    setDraft(next);
    try {
      await onSave(next);
      pendingSaved.current = JSON.stringify(next);
      setSaved(next);
    } catch {
      // The mutation error is rendered by the page; keep this draft dirty for retry.
    }
  };
  const searchedMarkets = extraMarkets.filter(([code, label]) => label.includes(search.trim()) || code.includes(search.trim().toUpperCase()));
  return <section className="surface mb-5 p-4" aria-label="项目工具栏">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-bold tracking-tight">{project.name}</h1><div className="flex flex-wrap gap-2"><button className="secondary-button" type="button" onClick={onAdd}>添加商品</button><button className="toolbar-choice" type="button" onClick={onSelectAll}>全选</button><button className="toolbar-choice" type="button" onClick={onDeselectAll}>取消全选</button><button className="toolbar-choice" type="button" onClick={onInvert}>反选</button><button className="secondary-button" type="button" disabled={!selectedCount} onClick={onPrepare}>预备生成（{selectedCount}）</button><button className="primary-button" type="button" disabled={!selectedCount} onClick={onGenerate}>正式生成（{selectedCount}）</button><Link className="secondary-button" to={`/projects/${project.id}/results`}>生产结果</Link></div></div>
    <div className="mt-4 grid gap-3 xl:grid-cols-[auto_1fr_auto_auto_minmax(180px,1fr)] xl:items-end">
      <fieldset><legend className="mb-1 text-xs font-medium text-slate-500">平台</legend><div className="flex flex-wrap gap-1">{platforms.map(([code, label]) => <button key={code} aria-pressed={draft.platform === code} className={`toolbar-choice ${draft.platform === code ? "toolbar-choice-active" : ""}`} type="button" disabled={pending} onClick={() => void save({ ...draft, platform: code })}>{label}</button>)}</div></fieldset>
      <fieldset className="min-w-0"><legend className="mb-1 text-xs font-medium text-slate-500">市场</legend><div className="flex flex-wrap gap-1">{commonMarkets.map(([code, label]) => <button key={code} aria-pressed={draft.market === code} className={`toolbar-choice ${draft.market === code ? "toolbar-choice-active" : ""}`} type="button" disabled={pending} onClick={() => void save({ ...draft, market: code })}>{label}</button>)}<span className="relative"><button className="toolbar-choice" type="button" aria-expanded={moreOpen} onClick={() => setMoreOpen((open) => !open)}>更多国家</button>{moreOpen && <span className="absolute left-0 top-full z-20 mt-2 block w-64 rounded-xl border border-slate-200 bg-white p-2 shadow-lg"><input aria-label="搜索更多国家" className="mb-2" placeholder="搜索或输入国家/地区" value={search} onChange={(event) => setSearch(event.target.value)} />{searchedMarkets.map(([code, label]) => <button className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-50" key={code} type="button" onClick={() => { void save({ ...draft, market: code }); setMoreOpen(false); }}>{label}</button>)}{search.trim() && <button className="mt-1 block w-full rounded-lg bg-slate-100 px-3 py-2 text-left text-sm font-semibold text-slate-700" type="button" onClick={() => { void save({ ...draft, market: marketValue(search) }); setMoreOpen(false); }}>使用“{search.trim()}”</button>}</span>}</span></div></fieldset>
      <label className="text-xs font-medium text-slate-500">比例<select aria-label="图片比例" className="mt-1 min-w-24" value={draft.size} onChange={(event) => void save({ ...draft, size: event.target.value })}><option value="1:1">1:1</option><option value="3:4">3:4</option></select></label>
      <label className="text-xs font-medium text-slate-500">分辨率<select aria-label="图片分辨率" className="mt-1 min-w-24" value={draft.resolution} onChange={(event) => void save({ ...draft, resolution: event.target.value })}><option value="1k">1K</option><option value="2k">2K</option></select></label>
      <label className="text-xs font-medium text-slate-500">项目风格<input aria-label="项目风格" className="mt-1" value={draft.globalPrompt} placeholder="全项目默认风格（选填）" onChange={(event) => setDraft({ ...draft, globalPrompt: event.target.value })} onBlur={() => { if (dirty) void save(draft); }} /></label>
    </div>
  </section>;
}

function ImportModal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; document.addEventListener("keydown", close); return () => document.removeEventListener("keydown", close); }, [onClose]);
  useEffect(() => { closeButton.current?.focus(); }, []);
  return <div className="fixed inset-0 z-30 grid place-items-center bg-slate-950/30 p-4" role="presentation" onMouseDown={onClose}><section aria-label="添加商品" aria-modal="true" className="add-product-modal max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-slate-50 p-5 shadow-2xl" role="dialog" onMouseDown={(event) => event.stopPropagation()}><div className="mb-4 flex items-center justify-between"><h2 className="text-lg font-semibold">添加商品</h2><button ref={closeButton} className="secondary-button" type="button" onClick={onClose}>关闭</button></div>{children}</section></div>;
}
