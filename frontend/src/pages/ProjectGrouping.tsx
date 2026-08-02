import { DndContext, KeyboardSensor, PointerSensor, useDroppable, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, deleteAsset, deleteCluster, generateProject, importSkus, mergeAsset, prepareProject, splitAsset, updateCluster, updateProjectSettings, uploadAssets, type UploadResult } from "../api";
import { ImportPanel } from "../components/ImportPanel";
import { ProductCard } from "../components/ProductCard";
import { commonMarkets, extraMarkets, platforms } from "../labels";
import { EmptyState, ErrorPanel, Shell } from "../layout";
import { useProjectSnapshot } from "../queries";
import type { ClusterUpdateInput, ImportMode, ProductAsset, ProductConfiguration, Project } from "../types";

const slotOrders = [1, 2, 3, 4, 5, 6, 7, 8, 9];

function isGlobalError(error: unknown) {
  return !(error instanceof ApiError) || error.authRequired || error.status === 401 || error.status === 403 || error.status >= 500;
}

export default function ProjectGrouping() {
  const { projectId } = useParams();
  const projectQuery = useProjectSnapshot(projectId);
  const queryClient = useQueryClient();
  const [deselectedIds, setDeselectedIds] = useState<Set<string>>(new Set());
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
  const markSelectedPreparing = () => {
    const ids = new Set(selectedClusters.map((sku) => sku.id));
    queryClient.setQueryData<Project>(["project", projectId], (current) => current ? {
      ...current,
      skus: current.skus.map((sku) => ids.has(sku.id) ? {
        ...sku,
        preparationStatus: "preparing",
        preparation: { status: "preparing", stage: "N1", current: 0, total: 7, error: "" },
      } : sku),
    } : current);
  };
  const prepare = useMutation({ mutationFn: () => prepareProject(projectId!, selectedClusters.map((sku) => sku.id)), onMutate: markSelectedPreparing, onSuccess: invalidate });
  const generate = useMutation({ mutationFn: () => generateProject(projectId!, { clusterIds: selectedClusters.map((sku) => sku.id), slotOrders }), onMutate: markSelectedPreparing, onSuccess: invalidate });
  const save = useMutation({ mutationFn: ({ skuId, expectedVersion, payload }: { skuId: string; expectedVersion: number; payload: ClusterUpdateInput }) => updateCluster(skuId, expectedVersion, payload), onSuccess: invalidate });
  const removeAsset = useMutation({ mutationFn: deleteAsset, onSuccess: invalidate });
  const removeCluster = useMutation({ mutationFn: deleteCluster, onSuccess: invalidate });
  const saveSettings = useMutation({ mutationFn: (input: ProductConfiguration) => updateProjectSettings(projectId!, input), onSuccess: invalidate });
  const reorganize = useMutation({
    onMutate: async ({ activeId, overId }) => {
      await queryClient.cancelQueries({ queryKey: ["project", projectId] });
      const previous = queryClient.getQueryData<Project>(["project", projectId]);
      queryClient.setQueryData<Project>(["project", projectId], (current) => {
        const assetId = activeId.startsWith("asset:") ? activeId.slice(6) : "";
        if (!current || !assetId) return current;
        const source = current.skus.find((sku) => sku.assetIds.includes(assetId));
        const overAssetId = overId.startsWith("asset-target:") ? overId.slice(13) : "";
        const target = overAssetId ? current.skus.find((sku) => sku.assetIds.includes(overAssetId)) : overId.startsWith("cluster:") ? current.skus.find((sku) => sku.id === overId.slice(8)) : null;
        if (!source || !target || (source.id !== target.id && target.assetIds.includes(assetId))) return current;
        const asset = current.assets.find((item) => item.id === assetId) ?? source.assets?.find((item) => item.id === assetId);
        const skus = current.skus.map((sku) => {
          if (sku.id === source.id && sku.id === target.id) {
            const nextIds = sku.assetIds.filter((id) => id !== assetId);
            const insertAt = overAssetId ? nextIds.indexOf(overAssetId) : nextIds.length;
            nextIds.splice(insertAt < 0 ? nextIds.length : insertAt, 0, assetId);
            const byId = new Map((sku.assets ?? []).map((item) => [item.id, item]));
            return { ...sku, assetIds: nextIds, assets: nextIds.map((id) => byId.get(id)).filter((item): item is ProductAsset => Boolean(item)) };
          }
          if (sku.id === source.id) return { ...sku, assetIds: sku.assetIds.filter((id) => id !== assetId), assets: sku.assets?.filter((item) => item.id !== assetId) };
          if (sku.id === target.id) {
            const nextIds = sku.assetIds.filter((id) => id !== assetId);
            const insertAt = overAssetId ? nextIds.indexOf(overAssetId) : nextIds.length;
            nextIds.splice(insertAt < 0 ? nextIds.length : insertAt, 0, assetId);
            const byId = new Map([...(sku.assets ?? []), asset].filter((item): item is ProductAsset => Boolean(item)).map((item) => [item.id, item]));
            return { ...sku, assetIds: nextIds, assets: nextIds.map((id) => byId.get(id)).filter((item): item is ProductAsset => Boolean(item)) };
          }
          return sku;
        }).filter((sku) => sku.assetIds.length);
        return { ...current, skus };
      });
      return { previous };
    },
    mutationFn: async ({ activeId, overId }: { activeId: string; overId: string }) => {
      if (activeId.startsWith("asset:") && overId === "blank-grid") return splitAsset(activeId.slice(6));
      if (activeId.startsWith("asset:") && overId.startsWith("asset-target:")) {
        const assetId = activeId.slice(6);
        const overAssetId = overId.slice(13);
        const source = project!.skus.find((sku) => sku.assetIds.includes(assetId));
        const target = project!.skus.find((sku) => sku.assetIds.includes(overAssetId));
        if (!source || !target) return;
        if (source.id !== target.id) return mergeAsset(target.id, assetId, target.version);
        const next = source.assetIds.filter((id) => id !== assetId);
        next.splice(next.indexOf(overAssetId), 0, assetId);
        if (next.every((id, index) => id === source.assetIds[index])) return;
        return updateCluster(source.id, source.version, { asset_order: next });
      }
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
    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(["project", projectId], context.previous);
    },
    onSettled: invalidate,
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
    <ProjectToolbar project={project} selectedCount={selectedClusters.length} pending={saveSettings.isPending} onSave={(input) => saveSettings.mutateAsync(input)} onSelectAll={() => setDeselectedIds(new Set())} onDeselectAll={() => setDeselectedIds(new Set(project.skus.map((sku) => sku.id)))} onInvert={() => setDeselectedIds(new Set(project.skus.filter((sku) => !deselectedIds.has(sku.id)).map((sku) => sku.id)))} />
    <div className="mb-5"><ImportPanel disabled={busy} onUpload={(files, mode) => upload.mutateAsync({ files, mode })} onSkuImport={(skus, mode) => skuImport.mutateAsync({ skus, mode })} onImported={() => undefined} /></div>
    {uploadResult && <div className="mb-5 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm">成功导入 {uploadResult.asset_count} 个素材{uploadResult.rejected.length ? `，${uploadResult.rejected.length} 个未导入` : ""}。</div>}
    {localError instanceof ApiError && <p className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{localError.message}</p>}
    {globalError && <div className="mb-5"><ErrorPanel error={globalError} /></div>}
    <DndContext sensors={sensors} onDragEnd={onDragEnd}><ProductGrid>{project.skus.map((sku) => {
      const assets = sku.assets ?? project.assets.filter((asset) => sku.assetIds.includes(asset.id));
      return <ProductCard key={sku.id} sku={sku} assets={assets} selected={!deselectedIds.has(sku.id)} expanded={expandedId === sku.id} disabled={save.isPending || removeAsset.isPending || removeCluster.isPending} onOpen={() => setExpandedId(sku.id)} onClose={() => setExpandedId(null)} onSave={(payload, expectedVersion) => save.mutateAsync({ skuId: sku.id, expectedVersion, payload })} onReload={() => projectQuery.refetch()} onDeleteAsset={(assetId) => removeAsset.mutate(assetId)} onDelete={() => removeCluster.mutate(sku.id)} onSelect={(next) => setDeselectedIds((current) => { const copy = new Set(current); if (next) copy.delete(sku.id); else copy.add(sku.id); return copy; })} />;
    })}</ProductGrid></DndContext>
    {!project.skus.length && <EmptyState title="还没有商品素材" description="在上方导入图片、文件夹或 ERP SKU。" />}
    <FloatingActions projectId={project.id} selectedCount={selectedClusters.length} busy={busy} onPrepare={() => prepare.mutate()} onGenerate={() => generate.mutate()} />
  </Shell>;
}

function ProductGrid({ children }: { children: ReactNode }) {
  const blank = useDroppable({ id: "blank-grid", data: { type: "blank" } });
  return <section ref={blank.setNodeRef} className={`product-card-grid min-h-56 rounded-2xl ${blank.isOver ? "bg-indigo-50" : ""}`} aria-label="商品分组网格">{children}</section>;
}

function ProjectToolbar({ project, selectedCount, pending, onSave, onSelectAll, onDeselectAll, onInvert }: { project: { id: string; name: string; defaultConfig?: ProductConfiguration; platform: string; market: string; size: string; resolution?: string }; selectedCount: number; pending: boolean; onSave: (input: ProductConfiguration) => Promise<unknown>; onSelectAll: () => void; onDeselectAll: () => void; onInvert: () => void }) {
  const allMarkets = [...commonMarkets, ...extraMarkets];
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
  return <section className="surface mb-3 p-2" aria-label="项目工具栏">
    <div className="grid gap-2 xl:grid-cols-[auto_auto_8.5rem_5.5rem_5.5rem_minmax(180px,1fr)_auto] xl:items-end">
      <h1 className="self-center truncate text-lg font-bold tracking-tight" title={project.name}>{project.name}</h1>
      <fieldset><legend className="mb-1 text-xs font-medium text-slate-500">平台</legend><div className="flex flex-nowrap gap-1">{platforms.map(([code, label]) => <button key={code} aria-pressed={draft.platform === code} className={`toolbar-choice whitespace-nowrap ${draft.platform === code ? "toolbar-choice-active" : ""}`} type="button" disabled={pending} onClick={() => void save({ ...draft, platform: code })}>{label}</button>)}</div></fieldset>
      <label className="text-xs font-medium text-slate-500">国家<select aria-label="项目国家" className="mt-1" value={draft.market} onChange={(event) => void save({ ...draft, market: event.target.value })}>{allMarkets.map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label>
      <label className="text-xs font-medium text-slate-500">比例<select aria-label="图片比例" className="mt-1" value={draft.size} onChange={(event) => void save({ ...draft, size: event.target.value })}><option value="1:1">1:1</option><option value="3:4">3:4</option></select></label>
      <label className="text-xs font-medium text-slate-500">分辨率<select aria-label="图片分辨率" className="mt-1" value={draft.resolution} onChange={(event) => void save({ ...draft, resolution: event.target.value })}><option value="1k">1K</option><option value="2k">2K</option></select></label>
      <label className="text-xs font-medium text-slate-500">项目风格提示词<textarea aria-label="项目风格提示词" className="mt-1 min-h-9 py-1.5" value={draft.globalPrompt} placeholder="全项目默认提示词（选填）" onChange={(event) => setDraft({ ...draft, globalPrompt: event.target.value })} onBlur={() => { if (dirty) void save(draft); }} /></label>
      <div className="flex flex-wrap justify-end gap-2"><button className="toolbar-choice" type="button" onClick={onSelectAll}>全选</button><button className="toolbar-choice" type="button" onClick={onDeselectAll}>取消全选</button><button className="toolbar-choice" type="button" onClick={onInvert}>反选</button><span className="rounded-lg bg-slate-100 px-3 py-2 text-xs font-semibold text-slate-600">已选 {selectedCount}</span><Link className="secondary-button min-h-8 px-3 text-xs" to={`/projects/${project.id}/results`}>生产结果</Link></div>
    </div>
  </section>;
}

function FloatingActions({ projectId, selectedCount, busy, onPrepare, onGenerate }: { projectId: string; selectedCount: number; busy: boolean; onPrepare: () => void; onGenerate: () => void }) {
  return <div className="fixed bottom-5 right-5 z-50 flex max-w-[calc(100vw-2.5rem)] flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-white/95 p-2 shadow-2xl shadow-slate-300/70 backdrop-blur" aria-label="滚动常驻生成动作">
    <span className="px-2 text-xs font-semibold text-slate-600">已选 {selectedCount}</span>
    <button className="secondary-button min-h-9 px-3" type="button" disabled={busy || !selectedCount} onClick={onPrepare}>预备生成（{selectedCount}）</button>
    <button className="primary-button min-h-9 px-3" type="button" disabled={busy || !selectedCount} onClick={onGenerate}>正式生成（{selectedCount}）</button>
    <Link className="secondary-button min-h-9 px-3" to={`/projects/${projectId}/results`}>生产结果</Link>
  </div>;
}
