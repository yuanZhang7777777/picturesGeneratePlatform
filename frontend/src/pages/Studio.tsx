import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError, confirmProject, preflightProject, updateCluster } from "../api";
import { ErrorPanel, PageHeading, Shell, statusText } from "../layout";
import { useProjectSnapshot } from "../queries";
import { currentOutputs } from "../workspace";

export default function Studio() {
  const { projectId, skuId } = useParams();
  const projectQuery = useProjectSnapshot(projectId);
  const queryClient = useQueryClient();
  const project = projectQuery.data;
  const sku = project?.skus.find((item) => item.id === skuId);
  const [facts, setFacts] = useState("");
  const [identityLock, setIdentityLock] = useState("");
  const [brief, setBrief] = useState("");
  const latest = useMemo(() => sku ? currentOutputs(sku.outputs) : [], [sku]);
  const [selectedGenerationId, setSelectedGenerationId] = useState("");
  useEffect(() => { if (sku) { setFacts(sku.facts); setIdentityLock(sku.identityLock); setBrief(sku.brief); setSelectedGenerationId(currentOutputs(sku.outputs)[0]?.id ?? ""); } }, [sku?.id, sku?.facts, sku?.identityLock, sku?.brief, sku?.outputs]);
  const persist = useMutation({ mutationFn: async () => { if (!sku || sku.version === undefined) throw new ApiError(409, "服务器项目快照缺少分组版本，无法安全保存 Brief。请刷新后重试。"); await updateCluster(sku.id, sku.version, { product_facts: facts, identity_lock: identityLock, prompt_override: brief }); }, onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["project", projectId] }); await queryClient.invalidateQueries({ queryKey: ["workspace"] }); } });
  const preflight = useMutation({ mutationFn: () => preflightProject(projectId!) });
  const generate = useMutation({ mutationFn: async () => { await persist.mutateAsync(); return confirmProject(projectId!); }, onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["project", projectId] }); await queryClient.invalidateQueries({ queryKey: ["workspace"] }); } });
  if (projectQuery.isLoading) return <Shell><PageHeading eyebrow="商品创作台" title="正在读取商品…" /></Shell>;
  if (projectQuery.isError || !project || !sku) return <Shell><PageHeading eyebrow="商品创作台" title="商品不可用" /><ErrorPanel error={projectQuery.error ?? new Error("未找到商品")} retry={() => void projectQuery.refetch()} /></Shell>;
  const selected = latest.find((output) => output.id === selectedGenerationId) ?? latest[0];
  const assets = project.assets.filter((asset) => sku.assetIds.includes(asset.id));
  const canConfirm = Boolean(preflight.data && preflight.data.blocking_errors.length === 0);
  return <Shell><PageHeading eyebrow={`${project.name} / ${sku.name}`} title="商品创作台" action={<Link className="secondary-button" to={`/projects/${project.id}`}>返回商品分组</Link>} /><div className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)_330px]"><aside className="surface p-4"><p className="section-label">商品参考图</p><div className="mt-3 grid grid-cols-2 gap-3">{assets.map((asset) => asset.imageUrl ? <img key={asset.id} className="aspect-square w-full rounded-xl object-cover" src={asset.imageUrl} alt={asset.name} /> : <div key={asset.id} className="aspect-square rounded-xl bg-slate-100" />)}</div><label className="mt-6 block text-sm font-medium text-slate-700"><span className="mb-2 block">身份锁</span><textarea value={identityLock} onChange={(event) => setIdentityLock(event.target.value)} /></label></aside><section className="surface p-5"><div className="flex items-center justify-between"><div><p className="section-label">当前输出</p><h2 className="mt-1 text-xl font-semibold">{selected?.name ?? "准备生成"}</h2></div><span className={`status status-${selected?.status ?? "draft"}`}>{statusText(selected?.status ?? "draft")}</span></div><div className="mt-5 grid min-h-96 place-items-center overflow-hidden rounded-2xl bg-slate-100">{selected?.imageUrl ? <img className="h-full max-h-[520px] w-full object-contain" src={selected.imageUrl} alt={selected.name} /> : <p className="text-sm text-slate-400">确认生成后将在这里显示结果</p>}</div><div className="mt-5 flex gap-2 overflow-x-auto">{latest.map((output) => <button key={output.id} aria-label={`${output.slot} 第${output.slotOrder}位 v${output.attempt}`} className={`slot-button ${selected?.id === output.id ? "slot-button-active" : ""}`} onClick={() => setSelectedGenerationId(output.id)}>{output.slot}<span>v{output.attempt}</span></button>)}</div></section><aside className="surface p-5"><p className="section-label">AI Brief</p><label className="mt-4 block text-sm font-medium text-slate-700"><span className="mb-2 block">商品卖点与规格</span><textarea value={facts} onChange={(event) => setFacts(event.target.value)} /></label><label className="mt-4 block text-sm font-medium text-slate-700"><span className="mb-2 block">画面说明</span><textarea value={brief} onChange={(event) => setBrief(event.target.value)} /></label>{persist.isError && <ErrorPanel error={persist.error} retry={() => persist.mutate()} />}{preflight.isError && <ErrorPanel error={preflight.error} retry={() => preflight.mutate()} />}{generate.isError && <ErrorPanel error={generate.error} retry={() => generate.mutate()} />}{preflight.data && <section className="mt-4 rounded-xl bg-slate-50 p-3 text-sm"><p>将生成 {preflight.data.generation_count} 张输出图</p><p className="mt-1 text-slate-500">{preflight.data.cluster_count} 个商品分组 × {preflight.data.slot_count} 个输出位</p>{preflight.data.blocking_errors.length > 0 && <ul className="mt-2 list-disc pl-5 text-rose-700">{preflight.data.blocking_errors.map((error) => <li key={error}>{error}</li>)}</ul>}</section>}<div className="mt-5 flex flex-wrap gap-2"><button className="secondary-button" disabled={preflight.isPending} onClick={() => preflight.mutate()}>{preflight.isPending ? "正在预检…" : "运行预检"}</button><button className="secondary-button" disabled={persist.isPending} onClick={() => persist.mutate()}>保存 Brief</button><button className="primary-button" disabled={!canConfirm || generate.isPending} onClick={() => generate.mutate()}>确认批量生成</button></div></aside></div></Shell>;
}
