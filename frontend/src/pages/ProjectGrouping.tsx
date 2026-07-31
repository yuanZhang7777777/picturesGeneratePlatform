import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DndContext, type DragEndEvent } from "@dnd-kit/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError, deleteAsset, deleteCluster, generateProject, importSkus, mergeAsset, updateCluster, updateProjectSettings, uploadAssets, type UploadResult } from "../api";
import { ImportPanel } from "../components/ImportPanel";
import { ProductCard } from "../components/ProductCard";
import { EmptyState, ErrorPanel, PageHeading, Shell } from "../layout";
import { useProjectSnapshot } from "../queries";
import type { ClusterUpdateInput, ImportMode, ProductConfiguration, ProductSku } from "../types";

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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const project = projectQuery.data;
  const selectedClusters = useMemo(() => project?.skus.filter((sku) => !deselectedIds.has(sku.id)) ?? [], [project, deselectedIds]);
  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    await queryClient.invalidateQueries({ queryKey: ["workspace"] });
  };
  const upload = useMutation({
    mutationFn: ({ files, mode }: { files: File[]; mode: ImportMode }) => uploadAssets(projectId!, files, mode),
    onSuccess: async (result) => { setUploadResult(result); await invalidate(); },
  });
  const skuImport = useMutation({ mutationFn: ({ skus, mode }: { skus: string[]; mode: ImportMode }) => importSkus(projectId!, skus, mode), onSuccess: invalidate });
  const generate = useMutation({ mutationFn: () => generateProject(projectId!, { clusterIds: selectedClusters.map((sku) => sku.id), slotOrders }), onSuccess: invalidate });
  const merge = useMutation({ mutationFn: ({ sku, assetId }: { sku: ProductSku; assetId: string }) => mergeAsset(sku.id, assetId, sku.version), onSuccess: invalidate });
  const save = useMutation({ mutationFn: ({ sku, payload }: { sku: ProductSku; payload: ClusterUpdateInput }) => updateCluster(sku.id, sku.version, payload), onSuccess: invalidate });
  const removeAsset = useMutation({ mutationFn: deleteAsset, onSuccess: invalidate });
  const removeCluster = useMutation({ mutationFn: deleteCluster, onSuccess: invalidate });
  const saveSettings = useMutation({ mutationFn: (input: ProductConfiguration) => updateProjectSettings(projectId!, input), onSuccess: async () => { setSettingsOpen(false); await invalidate(); } });

  if (projectQuery.isLoading) return <Shell><PageHeading eyebrow="项目工作区" title="正在读取项目…" /></Shell>;
  if (projectQuery.isError || !project) return <Shell><PageHeading eyebrow="项目工作区" title="项目不可用" /><ErrorPanel error={projectQuery.error ?? new Error("项目快照为空")} retry={() => void projectQuery.refetch()} /></Shell>;

  const assignedAssetIds = new Set(project.skus.flatMap((sku) => sku.assetIds));
  const mergeableAssets = project.assets.filter((asset) => asset.kind === "image" && !assignedAssetIds.has(asset.id));
  const selectedCount = selectedClusters.length;
  const imageCount = selectedCount * 9;
  const configured = project.configurationStatus === "configured";
  const onDragEnd = (event: DragEndEvent) => {
    if (!event.over) return;
    const target = project.skus.find((sku) => sku.id === String(event.over?.id));
    if (target) merge.mutate({ sku: target, assetId: String(event.active.id) });
  };
  const errors = [upload.error, skuImport.error, generate.error, merge.error, save.error, removeAsset.error, removeCluster.error, saveSettings.error].filter(Boolean);
  const localError = errors.find((error) => !isGlobalError(error));
  const globalError = errors.find(isGlobalError);

  return <Shell>
    <PageHeading eyebrow={configured ? `${project.platform} · ${project.market} · ${project.size}${project.resolution ? ` · ${project.resolution}` : ""}` : "请先设置平台和国家"} title={project.name} action={<Link className="secondary-button" to={`/projects/${project.id}/results`}>生产与结果</Link>} />
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div className="flex flex-wrap items-center gap-2"><button className="secondary-button" type="button" onClick={() => setSettingsOpen((open) => !open)}>项目默认配置</button><button className="secondary-button" type="button" onClick={() => setImportOpen((open) => !open)}>添加商品</button><span className="text-sm text-slate-500">{configured ? "每个商品生成 1 张白底标准图 + 8 张营销图。" : "导入后，在项目默认配置里选择平台和国家。"}</span></div>
      <div className="flex flex-wrap gap-2"><button className="secondary-button" onClick={() => setDeselectedIds(new Set())}>全选</button><button className="secondary-button" onClick={() => setDeselectedIds(new Set(project.skus.map((sku) => sku.id)))}>取消全选</button><button className="secondary-button" onClick={() => setDeselectedIds(new Set(project.skus.filter((sku) => !deselectedIds.has(sku.id)).map((sku) => sku.id)))}>反选</button><button className="primary-button" disabled={!configured || !selectedCount || generate.isPending} onClick={() => generate.mutate()}>生成选中商品（{selectedCount} 个商品 / {imageCount} 张图）</button></div>
    </div>
    {settingsOpen && <ProjectSettings initial={project.defaultConfig} pending={saveSettings.isPending} onSave={(input) => saveSettings.mutate(input)} />}
    {importOpen && <ImportDrawer onClose={() => setImportOpen(false)}><ImportPanel disabled={upload.isPending || skuImport.isPending || generate.isPending} onUpload={(files, mode) => upload.mutateAsync({ files, mode })} onSkuImport={(skus, mode) => skuImport.mutateAsync({ skus, mode })} onImported={() => setImportOpen(false)} /></ImportDrawer>}
    {uploadResult && <div className="mb-5 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm"><p>成功导入 {uploadResult.asset_count} 个素材{uploadResult.rejected.length ? `，${uploadResult.rejected.length} 个未导入` : ""}。</p>{uploadResult.rejected.length > 0 && <p className="mt-1 text-amber-700">失败项已保留在添加商品面板，可直接重试。</p>}</div>}
    {localError instanceof ApiError && <p className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{localError.message}</p>}
    {globalError && <div className="mb-5"><ErrorPanel error={globalError} /></div>}
    <DndContext onDragEnd={onDragEnd}><section className="product-card-grid">{project.skus.map((sku) => {
      const assets = sku.assets ?? project.assets.filter((asset) => sku.assetIds.includes(asset.id));
      return <ProductCard key={sku.id} sku={sku} assets={assets} mergeableAssets={mergeableAssets} selected={!deselectedIds.has(sku.id)} disabled={merge.isPending || save.isPending || removeAsset.isPending || removeCluster.isPending} onMerge={(assetId) => merge.mutate({ sku, assetId })} onSave={(payload) => save.mutate({ sku, payload })} onDeleteAsset={(assetId) => removeAsset.mutate(assetId)} onDelete={() => removeCluster.mutate(sku.id)} onSelect={(next) => setDeselectedIds((current) => { const copy = new Set(current); if (next) copy.delete(sku.id); else copy.add(sku.id); return copy; })} />;
    })}</section></DndContext>
    {!project.skus.length && <EmptyState title="还没有商品素材" description="点击“添加商品”上传图片、文件夹或导入 ERP SKU。" />}
  </Shell>;
}

function ImportDrawer({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  useEffect(() => { const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; document.addEventListener("keydown", close); return () => document.removeEventListener("keydown", close); }, [onClose]);
  return <div className="fixed inset-0 z-30 bg-slate-950/20" role="presentation" onMouseDown={onClose}><aside aria-label="添加商品" aria-modal="true" className="absolute inset-y-0 right-0 w-full max-w-xl overflow-y-auto bg-slate-50 p-5 shadow-2xl" role="dialog" onMouseDown={(event) => event.stopPropagation()}><div className="mb-4 flex items-center justify-between"><h2 className="text-lg font-semibold">添加商品</h2><button className="secondary-button" type="button" onClick={onClose}>关闭</button></div>{children}</aside></div>;
}

function ProjectSettings({ initial, pending, onSave }: { initial?: ProductConfiguration; pending: boolean; onSave: (input: ProductConfiguration) => void }) {
  const [input, setInput] = useState<ProductConfiguration>(initial ?? { platform: "", market: "", sellerTier: "general", size: "1:1", resolution: "1k", globalPrompt: "" });
  const [moreOpen, setMoreOpen] = useState(false);
  return <section className="surface mb-5 p-5"><form className="grid gap-3 md:grid-cols-5" onSubmit={(event) => { event.preventDefault(); onSave(input); }}><label className="text-xs font-medium text-slate-500">平台<select aria-label="项目平台" className="mt-1" value={input.platform} onChange={(event) => setInput({ ...input, platform: event.target.value })}><option value="">请选择</option><option value="shopee">Shopee</option><option value="tiktok">TikTok Shop</option></select></label><label className="text-xs font-medium text-slate-500">国家/站点<input aria-label="项目国家" className="mt-1" value={input.market} placeholder="例如 SG / US" onChange={(event) => setInput({ ...input, market: event.target.value.toUpperCase() })} /></label><label className="text-xs font-medium text-slate-500">比例<select className="mt-1" value={input.size} onChange={(event) => setInput({ ...input, size: event.target.value })}><option value="1:1">1:1</option><option value="3:4">3:4</option></select></label><label className="text-xs font-medium text-slate-500">分辨率<select className="mt-1" value={input.resolution} onChange={(event) => setInput({ ...input, resolution: event.target.value })}><option value="1k">1K</option><option value="2k">2K</option></select></label><div className="flex items-end"><button className="primary-button w-full" disabled={pending || !input.platform || !input.market} type="submit">保存配置</button></div><label className="text-xs font-medium text-slate-500 md:col-span-5">项目风格<textarea className="mt-1 min-h-18" value={input.globalPrompt} placeholder="此项目所有商品默认沿用的风格要求" onChange={(event) => setInput({ ...input, globalPrompt: event.target.value })} /></label><div className="md:col-span-5"><button className="text-sm font-semibold text-slate-600" type="button" aria-expanded={moreOpen} onClick={() => setMoreOpen((open) => !open)}>项目更多设置</button>{moreOpen && <label className="mt-3 block max-w-48 text-xs font-medium text-slate-500">店铺类型<select aria-label="项目店铺类型" className="mt-1" value={input.sellerTier} onChange={(event) => setInput({ ...input, sellerTier: event.target.value as "general" | "mall" })}><option value="general">普通店</option><option value="mall">Mall</option></select></label>}</div></form></section>;
}
