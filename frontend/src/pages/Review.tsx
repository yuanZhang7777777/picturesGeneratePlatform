import { useMemo, useRef, useState, type MouseEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { submitReview } from "../api";
import { EmptyState, ErrorPanel, PageHeading, Shell } from "../layout";
import { useWorkspaceSnapshot } from "../queries";
import type { ReviewAnnotation, ReviewDecision } from "../types";
import { currentOutputs } from "../workspace";

function normalizedCircle(event: MouseEvent<HTMLDivElement>, image: HTMLImageElement | null): ReviewAnnotation | null {
  const bounds = event.currentTarget.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return null;
  const naturalWidth = image?.naturalWidth || bounds.width;
  const naturalHeight = image?.naturalHeight || bounds.height;
  const scale = Math.min(bounds.width / naturalWidth, bounds.height / naturalHeight);
  const contentWidth = naturalWidth * scale;
  const contentHeight = naturalHeight * scale;
  const offsetX = (bounds.width - contentWidth) / 2;
  const offsetY = (bounds.height - contentHeight) / 2;
  const x = (event.clientX - bounds.left - offsetX) / contentWidth;
  const y = (event.clientY - bounds.top - offsetY) / contentHeight;
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  return { kind: "circle", rect: [Number(x.toFixed(4)), Number(y.toFixed(4)), 0.08, 0.08], color: "#e11d48", width: 2 };
}

export default function Review() {
  const workspace = useWorkspaceSnapshot();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [description, setDescription] = useState("");
  const [annotations, setAnnotations] = useState<ReviewAnnotation[]>([]);
  const [lastDecision, setLastDecision] = useState<ReviewDecision | null>(null);
  const image = useRef<HTMLImageElement>(null);
  const candidates = useMemo(() => workspace.data?.projects.flatMap((project) => project.skus.flatMap((sku) => currentOutputs(sku.outputs).filter((output) => output.status === "completed" && output.reviewStatus === "pending").map((output) => ({ project, sku, output })))) ?? [], [workspace.data]);
  const selected = candidates.find((candidate) => candidate.output.id === selectedId) ?? candidates[0];
  const review = useMutation({ mutationFn: ({ decision }: { decision: ReviewDecision }) => submitReview(selected!.output.id, { decision, issue_tags: [], description, annotations }), onSuccess: async () => { setDescription(""); setAnnotations([]); await queryClient.invalidateQueries({ queryKey: ["workspace"] }); await queryClient.invalidateQueries({ queryKey: ["project", selected?.project.id] }); } });
  if (workspace.isLoading) return <Shell><PageHeading eyebrow="人工审核" title="审核中心" /></Shell>;
  if (workspace.isError || !workspace.data) return <Shell><PageHeading eyebrow="人工审核" title="审核中心" /><ErrorPanel error={workspace.error ?? new Error("项目快照为空")} retry={() => void workspace.refetch()} /></Shell>;
  if (!selected) return <Shell><PageHeading eyebrow="人工审核" title="审核中心" /><EmptyState title="没有待审核结果" description="生成完成的商品图会在这里等待确认。" /></Shell>;
  return <Shell><PageHeading eyebrow="人工审核" title="审核中心" />{review.isError && <ErrorPanel error={review.error} retry={() => { if (lastDecision) review.mutate({ decision: lastDecision }); }} />}<div className="grid gap-6 xl:grid-cols-[270px_minmax(0,1fr)_300px]"><aside className="surface p-3"><p className="section-label px-2 py-2">待判断输出图</p>{candidates.map((candidate) => <button key={candidate.output.id} onClick={() => { setSelectedId(candidate.output.id); setAnnotations([]); setDescription(""); }} className={`review-item ${selected.output.id === candidate.output.id ? "review-item-active" : ""}`}><span>{candidate.sku.name}</span><small>{candidate.output.slot} · v{candidate.output.attempt}</small></button>)}</aside><section className="surface p-5"><div className="flex items-center justify-between"><div><p className="section-label">点击结果图圈选问题</p><h2 className="mt-1 font-semibold">{selected.sku.name} · {selected.output.name}</h2></div><span className="status status-completed">待审核</span></div><div aria-label="圈选图片问题" onClick={(event) => { const mark = normalizedCircle(event, image.current); if (mark) setAnnotations((current) => [...current, mark]); }} className="review-canvas mt-5">{selected.output.imageUrl ? <img ref={image} src={selected.output.imageUrl} alt="待审核结果" /> : <span>结果图预览</span>}{annotations.map((annotation, index) => annotation.rect ? <i key={`${annotation.rect[0]}-${annotation.rect[1]}`} className="review-mark" style={{ left: `${(annotation.rect[0] + annotation.rect[2] / 2) * 100}%`, top: `${(annotation.rect[1] + annotation.rect[3] / 2) * 100}%` }}>{index + 1}</i> : null)}</div></section><aside className="surface p-5"><p className="section-label">审核结论</p><p className="mt-2 text-sm text-slate-500">圈选内容只会作为下一版修改指令提交，不会写入导出图片。</p><label className="mt-4 block text-sm font-medium text-slate-700"><span className="mb-2 block">修改说明</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明需要调整的地方" /></label><div className="mt-4 grid grid-cols-2 gap-2"><button className="secondary-button justify-center" disabled={review.isPending} onClick={() => { setLastDecision("changes_requested"); review.mutate({ decision: "changes_requested" }); }}>请求修改</button><button className="primary-button justify-center" disabled={review.isPending} onClick={() => { setLastDecision("accept"); review.mutate({ decision: "accept" }); }}>通过</button></div></aside></div></Shell>;
}
