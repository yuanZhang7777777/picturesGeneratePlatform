import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { retryGeneration } from "../api";
import { ErrorPanel, PageHeading, Shell, statusText } from "../layout";
import { useWorkspaceSnapshot } from "../queries";
import { currentOutputs } from "../workspace";

export default function Production() {
  const workspace = useWorkspaceSnapshot();
  const queryClient = useQueryClient();
  const [retryId, setRetryId] = useState("");
  const retry = useMutation({ mutationFn: retryGeneration, onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["workspace"] }); await queryClient.invalidateQueries({ queryKey: ["project"] }); } });
  if (workspace.isLoading) return <Shell><PageHeading eyebrow="批量生产" title="生产队列" /></Shell>;
  if (workspace.isError || !workspace.data) return <Shell><PageHeading eyebrow="批量生产" title="生产队列" /><ErrorPanel error={workspace.error ?? new Error("项目快照为空")} retry={() => void workspace.refetch()} /></Shell>;
  const rows = workspace.data.projects.flatMap((project) => project.skus.map((sku) => ({ project, sku, outputs: currentOutputs(sku.outputs) })));
  return <Shell><PageHeading eyebrow="批量生产" title="生产队列" />{retry.isError && <ErrorPanel error={retry.error} retry={() => { if (retryId) retry.mutate(retryId); }} />}<section className="surface overflow-hidden"><div className="overflow-x-auto"><table><thead><tr><th>商品</th><th>项目</th><th>套图完成度</th><th>当前状态</th><th>操作</th></tr></thead><tbody>{rows.map(({ project, sku, outputs }) => { const done = outputs.filter((output) => output.status === "completed").length; const active = outputs.find((output) => output.status === "running" || output.status === "failed" || output.status === "queued"); return <tr key={sku.id}><td><Link className="font-medium text-slate-950" to={`/projects/${project.id}/studio/${sku.id}`}>{sku.name}</Link></td><td>{project.name}</td><td>{done} / {outputs.length || "—"}</td><td><span className={`status status-${active?.status ?? "draft"}`}>{statusText(active?.status ?? "draft")}</span>{active?.failureReason && <span className="ml-2 text-xs text-rose-600">{active.failureReason}</span>}</td><td>{active?.status === "failed" ? <button className="text-sm font-semibold text-indigo-700" onClick={() => { setRetryId(active.id); retry.mutate(active.id); }}>只重做失败图</button> : <Link className="text-sm font-semibold text-indigo-700" to={`/projects/${project.id}/studio/${sku.id}`}>处理商品</Link>}</td></tr>; })}</tbody></table></div></section></Shell>;
}
