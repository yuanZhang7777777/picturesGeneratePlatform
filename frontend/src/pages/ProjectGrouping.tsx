import { useMemo, useState } from "react";
import { DndContext, type DragEndEvent } from "@dnd-kit/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import {
  deleteAsset,
  deleteCluster,
  generateProject,
  importSkus,
  mergeAsset,
  updateCluster,
  uploadAssets,
  type UploadResult,
} from "../api";
import { ImportPanel } from "../components/ImportPanel";
import { ProductCard } from "../components/ProductCard";
import { EmptyState, ErrorPanel, PageHeading, Shell } from "../layout";
import { useProjectSnapshot } from "../queries";
import type { ClusterUpdateInput, ImportMode, ProductSku } from "../types";

const slotOrders = [1, 2, 3, 4, 5, 6, 7, 8, 9];

export default function ProjectGrouping() {
  const { projectId } = useParams();
  const projectQuery = useProjectSnapshot(projectId);
  const queryClient = useQueryClient();
  const [deselectedIds, setDeselectedIds] = useState<Set<string>>(new Set());
  const [undoVisible, setUndoVisible] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const project = projectQuery.data;
  const selectedClusters = useMemo(() => {
    return project?.skus.filter((sku) => !deselectedIds.has(sku.id)) ?? [];
  }, [project, deselectedIds]);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    await queryClient.invalidateQueries({ queryKey: ["workspace"] });
  };
  const upload = useMutation({
    mutationFn: ({ files, mode }: { files: File[]; mode: ImportMode }) => uploadAssets(projectId!, files, mode),
    onSuccess: async (result) => {
      setUploadResult(result);
      await invalidate();
    },
  });
  const skuImport = useMutation({
    mutationFn: ({ skus, mode }: { skus: string[]; mode: ImportMode }) => importSkus(projectId!, skus, mode),
    onSuccess: async () => {
      await invalidate();
    },
  });
  const generate = useMutation({
    mutationFn: () => generateProject(projectId!, { clusterIds: selectedClusters.map((sku) => sku.id), slotOrders }),
    onSuccess: invalidate,
  });
  const merge = useMutation({
    mutationFn: ({ sku, assetId }: { sku: ProductSku; assetId: string }) => mergeAsset(sku.id, assetId, sku.version),
    onSuccess: async () => {
      setUndoVisible(true);
      await invalidate();
    },
  });
  const save = useMutation({
    mutationFn: ({ sku, payload }: { sku: ProductSku; payload: ClusterUpdateInput }) => updateCluster(sku.id, sku.version, payload),
    onSuccess: invalidate,
  });
  const removeAsset = useMutation({
    mutationFn: (assetId: string) => deleteAsset(assetId),
    onSuccess: invalidate,
  });
  const removeCluster = useMutation({
    mutationFn: (clusterId: string) => deleteCluster(clusterId),
    onSuccess: invalidate,
  });

  if (projectQuery.isLoading) return <Shell><PageHeading eyebrow="项目工作区" title="正在读取项目…" /></Shell>;
  if (projectQuery.isError || !project) return <Shell><PageHeading eyebrow="项目工作区" title="项目不可用" /><ErrorPanel error={projectQuery.error ?? new Error("项目快照为空")} retry={() => void projectQuery.refetch()} /></Shell>;

  const assignedAssetIds = new Set(project.skus.flatMap((sku) => sku.assetIds));
  const mergeableAssets = project.assets.filter((asset) => asset.kind === "image" && !assignedAssetIds.has(asset.id));
  const selectedCount = selectedClusters.length;
  const imageCount = selectedCount * 9;
  const onDragEnd = (event: DragEndEvent) => {
    if (!event.over) return;
    const target = project.skus.find((sku) => sku.id === String(event.over?.id));
    if (target) merge.mutate({ sku: target, assetId: String(event.active.id) });
  };

  return (
    <Shell>
      <PageHeading
        eyebrow={`${project.platform} · ${project.market} · ${project.size}${project.resolution ? ` · ${project.resolution}` : ""}`}
        title={project.name}
        action={<Link className="secondary-button" to={`/projects/${project.id}/results`}>生产与结果</Link>}
      />
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3">
        <p className="text-sm text-slate-600">每个商品生成 1 张白底标准图 + 8 张营销图。</p>
        <div className="flex flex-wrap gap-2">
          <button className="secondary-button" onClick={() => setDeselectedIds(new Set())}>全选</button>
          <button className="secondary-button" onClick={() => setDeselectedIds(new Set(project.skus.map((sku) => sku.id)))}>取消全选</button>
          <button
            className="secondary-button"
            onClick={() => setDeselectedIds(new Set(project.skus.filter((sku) => !deselectedIds.has(sku.id)).map((sku) => sku.id)))}
          >
            反选
          </button>
          {undoVisible && <button className="secondary-button" onClick={() => setUndoVisible(false)}>撤销上次合并</button>}
          <button className="primary-button" disabled={!selectedCount || generate.isPending} onClick={() => generate.mutate()}>
            生成选中商品（{selectedCount} 个商品 / {imageCount} 张图）
          </button>
        </div>
      </div>
      <ImportPanel
        disabled={upload.isPending || skuImport.isPending || generate.isPending}
        onUpload={(files, mode) => upload.mutateAsync({ files, mode })}
        onSkuImport={(skus, mode) => skuImport.mutate({ skus, mode })}
      />
      <div className="mt-6 space-y-3">
        {uploadResult && (
          <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm">
            <p>成功导入 {uploadResult.asset_count} 个素材{uploadResult.rejected.length ? `，${uploadResult.rejected.length} 个未导入` : ""}。</p>
            {uploadResult.rejected.map((item, index) => <p className="mt-1 text-amber-700" key={`${item.filename}:${item.code}`}>第 {index + 1} 个文件：{item.message}</p>)}
            {uploadResult.rejected.length > 0 && <p className="mt-2 text-xs text-slate-500">失败项已保留在上传区，可直接重试。</p>}
          </div>
        )}
        {upload.isError && (
          <div>
            <ErrorPanel error={upload.error} />
            <p className="mt-2 text-sm text-slate-600">待导入文件仍保留在上传区，请在那里重新提交。</p>
          </div>
        )}
        {skuImport.isError && <ErrorPanel error={skuImport.error} retry={() => void projectQuery.refetch()} />}
        {generate.isError && <ErrorPanel error={generate.error} retry={() => generate.mutate()} />}
        {merge.isError && <ErrorPanel error={merge.error} retry={() => void projectQuery.refetch()} />}
        {save.isError && <ErrorPanel error={save.error} retry={() => void projectQuery.refetch()} />}
        {removeAsset.isError && <ErrorPanel error={removeAsset.error} retry={() => void projectQuery.refetch()} />}
        {removeCluster.isError && <ErrorPanel error={removeCluster.error} retry={() => void projectQuery.refetch()} />}
      </div>
      <DndContext onDragEnd={onDragEnd}>
        <section className="mt-6 space-y-3">
          {project.skus.map((sku) => {
            const assets = sku.assets ?? project.assets.filter((asset) => sku.assetIds.includes(asset.id));
            const checked = !deselectedIds.has(sku.id);
            return (
              <ProductCard
                key={sku.id}
                sku={sku}
                assets={assets}
                mergeableAssets={mergeableAssets}
                selected={checked}
                disabled={merge.isPending || save.isPending || removeAsset.isPending || removeCluster.isPending}
                onMerge={(assetId) => merge.mutate({ sku, assetId })}
                onSave={(payload) => save.mutate({ sku, payload })}
                onDeleteAsset={(assetId) => removeAsset.mutate(assetId)}
                onDelete={() => removeCluster.mutate(sku.id)}
                onSelect={(next) => setDeselectedIds((current) => {
                  const copy = new Set(current);
                  if (next) copy.delete(sku.id);
                  else copy.add(sku.id);
                  return copy;
                })}
              />
            );
          })}
        </section>
      </DndContext>
      {!project.skus.length && <EmptyState title="还没有商品素材" description="上传图片或导入 ERP SKU 后，每个商品会出现在这里。" />}
    </Shell>
  );
}
