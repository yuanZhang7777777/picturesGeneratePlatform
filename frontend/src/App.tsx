import { DndContext, useDraggable, useDroppable, type DragEndEvent } from "@dnd-kit/core";
import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { createProject as createProjectRequest, loadWorkspace, uploadAssets } from "./api";
import { developmentWorkspace } from "./mock-data";
import type { GenerationStatus, ProductAsset, ProductSku, Project, ProjectInput, ReviewStatus, WorkspaceSnapshot } from "./types";
import { moveAssetToSku } from "./workspace";

type WorkspaceActions = {
  workspace: WorkspaceSnapshot;
  addProject: (input: ProjectInput) => Promise<Project>;
  addAssets: (projectId: string, files: File[]) => Promise<void>;
  moveAsset: (projectId: string, assetId: string, skuId: string) => void;
  updateSku: (projectId: string, skuId: string, change: Partial<ProductSku>) => void;
  updateReview: (projectId: string, skuId: string, outputId: string, reviewStatus: ReviewStatus) => void;
};

const WorkspaceContext = createContext<WorkspaceActions | null>(null);

function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Workspace context is missing");
  return value;
}

function updateProject(snapshot: WorkspaceSnapshot, projectId: string, change: (project: Project) => Project) {
  return { ...snapshot, projects: snapshot.projects.map((project) => (project.id === projectId ? change(project) : project)) };
}

function WorkspaceProvider({ children }: { children: ReactNode }) {
  const query = useQuery({ queryKey: ["workspace"], queryFn: loadWorkspace, staleTime: 30_000 });
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot>(developmentWorkspace);

  useEffect(() => {
    if (query.data) setWorkspace(query.data);
  }, [query.data]);

  const value = useMemo<WorkspaceActions>(
    () => ({
      workspace,
      addProject: async (input) => {
        const project = await createProjectRequest(input);
        setWorkspace((current) => ({ ...current, projects: [project, ...current.projects] }));
        return project;
      },
      addAssets: async (projectId, files) => {
        try {
          await uploadAssets(projectId, files);
        } catch {
          // Keep the development workspace usable while Django APIs are not mounted.
        }
        const assets: ProductAsset[] = files.map((file) => ({
          id: `asset-local-${crypto.randomUUID()}`,
          name: file.name,
          imageUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined,
          kind: file.type === "text/plain" ? "txt" : "image",
        }));
        setWorkspace((current) =>
          updateProject(current, projectId, (project) => ({
            ...project,
            assets: [...project.assets, ...assets],
            skus: [
              ...project.skus,
              ...assets.filter((asset) => asset.kind !== "txt").map((asset, index) => ({
                id: `sku-local-${crypto.randomUUID()}`,
                name: `未命名商品 ${project.skus.length + index + 1}`,
                assetIds: [asset.id],
                facts: "",
                identityLock: "",
                brief: "",
                outputs: [],
              })),
            ],
          })),
        );
      },
      moveAsset: (projectId, assetId, skuId) =>
        setWorkspace((current) =>
          updateProject(current, projectId, (project) => ({
            ...project,
            skus: moveAssetToSku(project.skus, assetId, skuId),
          })),
        ),
      updateSku: (projectId, skuId, change) =>
        setWorkspace((current) =>
          updateProject(current, projectId, (project) => ({
            ...project,
            skus: project.skus.map((sku) => (sku.id === skuId ? { ...sku, ...change } : sku)),
          })),
        ),
      updateReview: (projectId, skuId, outputId, reviewStatus) =>
        setWorkspace((current) =>
          updateProject(current, projectId, (project) => ({
            ...project,
            skus: project.skus.map((sku) =>
              sku.id !== skuId
                ? sku
                : { ...sku, outputs: sku.outputs.map((output) => (output.id === outputId ? { ...output, reviewStatus } : output)) },
            ),
          })),
        ),
    }),
    [workspace],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-60 flex-col border-r border-slate-200 bg-white px-4 py-5 lg:flex">
        <Link to="/" className="mb-9 flex items-center gap-3 px-2 text-lg font-bold tracking-tight text-slate-950">
          <span className="grid size-8 place-items-center rounded-xl bg-indigo-600 text-sm text-white">图</span>
          Prompt OS
        </Link>
        <nav className="space-y-1" aria-label="主导航">
          <Navigation to="/" label="工作台" />
          <Navigation to="/projects/new" label="新建项目" />
          <Navigation to="/production" label="生产队列" />
          <Navigation to="/review" label="审核中心" />
        </nav>
        <div className="mt-auto rounded-2xl bg-slate-100 p-3 text-xs leading-5 text-slate-500">内部运营工作台<br />按商品分组，再批量生产。</div>
      </aside>
      <div className="lg:pl-60">
        <header className="sticky top-0 z-10 flex h-16 items-center justify-between border-b border-slate-200/80 bg-slate-50/90 px-5 backdrop-blur lg:px-8">
          <p className="text-sm text-slate-500">商品图生产中心</p>
          <div className="flex items-center gap-3"><span className="status status-running">运行中</span><span className="grid size-8 place-items-center rounded-full bg-slate-900 text-xs font-semibold text-white">OP</span></div>
        </header>
        <main className="mx-auto max-w-7xl p-5 lg:p-8">{children}</main>
      </div>
    </div>
  );
}

function Navigation({ to, label }: { to: string; label: string }) {
  return <NavLink end={to === "/"} to={to} className={({ isActive }) => `nav-link ${isActive ? "nav-link-active" : ""}`}>{label}</NavLink>;
}

function PageHeading({ eyebrow, title, action }: { eyebrow: string; title: string; action?: ReactNode }) {
  return <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><p className="mb-1 text-xs font-semibold uppercase tracking-[0.16em] text-indigo-600">{eyebrow}</p><h1 className="text-3xl font-bold tracking-tight text-slate-950">{title}</h1></div>{action}</div>;
}

function Dashboard() {
  const { workspace } = useWorkspace();
  const totals = workspace.projects.reduce((summary, project) => {
    project.skus.forEach((sku) => sku.outputs.forEach((output) => {
      summary.total += 1;
      if (output.status === "running") summary.running += 1;
      if (output.status === "completed" && output.reviewStatus === "pending") summary.review += 1;
      if (output.status === "failed") summary.failed += 1;
    }));
    return summary;
  }, { total: 0, running: 0, review: 0, failed: 0 });
  return <Shell><PageHeading eyebrow="运营工作台" title="工作台" action={<Link className="primary-button" to="/projects/new">新建出图项目</Link>} />
    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Metric label="输出图任务" value={totals.total} note="当前项目" /><Metric label="生成中" value={totals.running} note="可进入队列查看" /><Metric label="待审核" value={totals.review} note="需要人工判断" /><Metric label="异常项" value={totals.failed} note="只重做失败图" /></section>
    <section className="mt-9"><div className="mb-4 flex items-center justify-between"><h2 className="text-lg font-semibold">最近项目</h2><span className="text-sm text-slate-500">按更新时间排序</span></div><div className="grid gap-4 lg:grid-cols-2">{workspace.projects.map((project) => <ProjectCard key={project.id} project={project} />)}</div></section>
  </Shell>;
}

function Metric({ label, value, note }: { label: string; value: number; note: string }) {
  return <article className="surface p-5"><p className="text-sm text-slate-500">{label}</p><p className="mt-2 text-3xl font-bold tracking-tight">{value}</p><p className="mt-2 text-xs text-slate-400">{note}</p></article>;
}

function ProjectCard({ project }: { project: Project }) {
  const complete = project.skus.flatMap((sku) => sku.outputs).filter((output) => output.status === "completed").length;
  const total = project.skus.flatMap((sku) => sku.outputs).length;
  return <Link to={`/projects/${project.id}`} className="surface group block p-5 transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-lg hover:shadow-indigo-100"><div className="flex items-start justify-between gap-4"><div><h3 className="text-lg font-semibold group-hover:text-indigo-700">{project.name}</h3><p className="mt-1 text-sm text-slate-500">{project.platform} · {project.market} · {project.template}</p></div><span className={`status status-${project.status}`}>{statusText(project.status)}</span></div><div className="mt-6 flex items-end justify-between"><div><p className="text-2xl font-bold">{complete}<span className="text-sm font-medium text-slate-400"> / {total || "—"}</span></p><p className="text-xs text-slate-400">已完成输出图</p></div><p className="text-xs text-slate-400">{project.updatedAt}</p></div></Link>;
}

function ProjectNew() {
  const navigate = useNavigate();
  const { addProject } = useWorkspace();
  const [input, setInput] = useState<ProjectInput>({ name: "未命名出图项目", platform: "Shopee", market: "SG", template: "商品基础套图", size: "1:1 · 1K" });
  const [saving, setSaving] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    const project = await addProject(input);
    navigate(`/projects/${project.id}`);
  };
  return <Shell><PageHeading eyebrow="新建项目" title="创建出图项目" /><form onSubmit={submit} className="mx-auto max-w-3xl space-y-6"><section className="surface p-6"><div className="grid gap-5 sm:grid-cols-2"><Field label="项目名称"><input id="project-name" aria-label="项目名称" value={input.name} onChange={(event) => setInput({ ...input, name: event.target.value })} /></Field><Field label="平台"><select value={input.platform} onChange={(event) => setInput({ ...input, platform: event.target.value })}><option>Shopee</option><option>TikTok Shop</option></select></Field><Field label="市场"><select value={input.market} onChange={(event) => setInput({ ...input, market: event.target.value })}><option>SG</option><option>MY</option><option>TH</option><option>VN</option><option>PH</option><option>ID</option></select></Field><Field label="套图模板"><select value={input.template} onChange={(event) => setInput({ ...input, template: event.target.value })}><option>商品基础套图</option><option>白底主图套图</option><option>营销场景套图</option></select></Field><Field label="比例与分辨率"><select value={input.size} onChange={(event) => setInput({ ...input, size: event.target.value })}><option>1:1 · 1K</option><option>3:4 · 1K</option><option>1:1 · 2K</option></select></Field></div></section><section className="surface p-6"><h2 className="font-semibold">下一步</h2><p className="mt-2 text-sm leading-6 text-slate-500">创建后先上传文件夹并完成商品分组。每张图默认是一件商品，多角度图可拖到同一商品卡。</p><button className="primary-button mt-5" disabled={saving} type="submit">{saving ? "正在创建…" : "创建项目并上传素材"}</button></section></form></Shell>;
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="block text-sm font-medium text-slate-700"><span className="mb-2 block">{label}</span>{children}</label>; }

function ProjectGrouping() {
  const { projectId } = useParams();
  const { workspace, addAssets, moveAsset } = useWorkspace();
  const project = workspace.projects.find((item) => item.id === projectId);
  const input = useRef<HTMLInputElement>(null);
  if (!project) return <NotFound />;
  const onFiles = async (files: FileList | null) => { if (files?.length) await addAssets(project.id, Array.from(files)); };
  const onDragEnd = (event: DragEndEvent) => { if (event.over) moveAsset(project.id, String(event.active.id), String(event.over.id)); };
  return <Shell><PageHeading eyebrow={`${project.platform} · ${project.market}`} title={project.name} action={<Link className="secondary-button" to={`/projects/${project.id}/studio/${project.skus[0]?.id ?? ""}`}>进入商品创作台</Link>} />
    <section className="mb-7 rounded-2xl border border-indigo-100 bg-indigo-50 px-5 py-4 text-sm text-indigo-950"><strong>识别结果：</strong> {project.assets.filter((asset) => asset.kind !== "txt").length} 张图片，{project.skus.length} 个商品分组。拖动图片到目标商品卡，即可合并多角度参考图。</section>
    <section className="surface mb-7 p-5"><div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center"><div><h2 className="font-semibold">上传商品素材</h2><p className="mt-1 text-sm text-slate-500">选择图片、TXT 或整个文件夹；上传结果先进入分组，不会自动生成。</p></div><div><input ref={input} className="sr-only" aria-label="选择图片或文件夹" type="file" multiple onChange={(event) => void onFiles(event.target.files)} /><button className="primary-button" type="button" onClick={() => input.current?.click()}>选择文件夹或图片</button></div></div></section>
    <DndContext onDragEnd={onDragEnd}><section className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">{project.skus.map((sku) => <SkuCard key={sku.id} sku={sku} assets={project.assets.filter((asset) => sku.assetIds.includes(asset.id))} projectId={project.id} />)}</section></DndContext>
    {!project.skus.length && <EmptyState title="还没有商品素材" description="上传图片后，每张图会自动成为一个商品分组。" />}
  </Shell>;
}

function SkuCard({ sku, assets, projectId }: { sku: ProductSku; assets: ProductAsset[]; projectId: string }) {
  const { setNodeRef, isOver } = useDroppable({ id: sku.id });
  return <article ref={setNodeRef} className={`surface min-h-72 p-4 ${isOver ? "ring-2 ring-indigo-500" : ""}`}><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">商品 / SKU</p><h2 className="mt-1 font-semibold text-slate-950">{sku.name}</h2></div><Link className="text-sm font-medium text-indigo-700" to={`/projects/${projectId}/studio/${sku.id}`}>编辑</Link></div><div className="mt-4 grid grid-cols-2 gap-3">{assets.map((asset) => <DraggableAsset key={asset.id} asset={asset} />)}</div><p className="mt-4 rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500">{assets.length ? "拖入更多角度图作为参考" : "把一张图片拖到此处合并"}</p></article>;
}

function DraggableAsset({ asset }: { asset: ProductAsset }) {
  const { attributes, listeners, setNodeRef, transform } = useDraggable({ id: asset.id });
  const style = transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : undefined;
  return <button ref={setNodeRef} style={style} {...listeners} {...attributes} className="overflow-hidden rounded-xl border border-slate-200 bg-white text-left shadow-sm"><AssetPreview asset={asset} /><span className="block truncate px-2 py-2 text-xs text-slate-600">{asset.name}</span></button>;
}

function AssetPreview({ asset }: { asset: ProductAsset }) { return asset.imageUrl ? <img className="aspect-square w-full object-cover" src={asset.imageUrl} alt={asset.name} /> : <div className="grid aspect-square place-items-center bg-slate-100 text-xs text-slate-400">{asset.kind === "txt" ? "TXT" : "待预览"}</div>; }

function Studio() {
  const { projectId, skuId } = useParams();
  const { workspace, updateSku } = useWorkspace();
  const project = workspace.projects.find((item) => item.id === projectId);
  const sku = project?.skus.find((item) => item.id === skuId);
  const [selectedSlot, setSelectedSlot] = useState("主图");
  const [saved, setSaved] = useState(false);
  if (!project || !sku) return <NotFound />;
  const assets = project.assets.filter((asset) => sku.assetIds.includes(asset.id));
  const currentOutput = sku.outputs.find((output) => output.slot === selectedSlot) ?? sku.outputs[0];
  const save = () => { updateSku(project.id, sku.id, sku); setSaved(true); };
  return <Shell><PageHeading eyebrow={`${project.name} / ${sku.name}`} title="商品创作台" action={<Link className="secondary-button" to={`/projects/${project.id}`}>返回商品分组</Link>} />
    <div className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)_330px]"><aside className="surface p-4"><p className="section-label">商品参考图</p><div className="mt-3 grid grid-cols-2 gap-3">{assets.map((asset) => <AssetPreview key={asset.id} asset={asset} />)}</div><div className="mt-6"><p className="section-label">身份锁</p><textarea className="mt-2" value={sku.identityLock} onChange={(event) => updateSku(project.id, sku.id, { identityLock: event.target.value })} /></div></aside>
      <section className="surface p-5"><div className="flex items-center justify-between"><div><p className="section-label">当前输出</p><h2 className="mt-1 text-xl font-semibold">{currentOutput?.name ?? "准备生成"}</h2></div><span className={`status status-${currentOutput?.status ?? "draft"}`}>{statusText(currentOutput?.status ?? "draft")}</span></div><div className="mt-5 grid min-h-96 place-items-center overflow-hidden rounded-2xl bg-slate-100">{currentOutput?.imageUrl ? <img className="h-full max-h-[520px] w-full object-contain" src={currentOutput.imageUrl} alt={currentOutput.name} /> : <p className="text-sm text-slate-400">选择槽位后生成预览</p>}</div><div className="mt-5 flex gap-2 overflow-x-auto">{sku.outputs.map((output) => <button key={output.id} aria-label={output.slot} className={`slot-button ${selectedSlot === output.slot ? "slot-button-active" : ""}`} onClick={() => setSelectedSlot(output.slot)}>{output.slot}<span>v{output.version}</span></button>)}</div></section>
      <aside className="surface p-5"><p className="section-label">AI Brief</p><Field label="商品卖点与规格"><textarea aria-label="商品卖点与规格" value={sku.facts} onChange={(event) => updateSku(project.id, sku.id, { facts: event.target.value })} /></Field><Field label="画面说明"><textarea aria-label="画面说明" value={sku.brief} onChange={(event) => updateSku(project.id, sku.id, { brief: event.target.value })} /></Field><div className="mt-5 flex gap-2"><button className="primary-button" onClick={save}>保存 Brief</button><button className="secondary-button" onClick={() => setSaved(false)}>生成当前槽位</button></div>{saved && <p className="mt-3 text-sm font-medium text-emerald-700">Brief 已保存，可用于下一次生成。</p>}</aside>
    </div></Shell>;
}

function Production() {
  const { workspace } = useWorkspace();
  const rows = workspace.projects.flatMap((project) => project.skus.map((sku) => ({ project, sku })));
  return <Shell><PageHeading eyebrow="批量生产" title="生产队列" /><section className="surface overflow-hidden"><div className="overflow-x-auto"><table><thead><tr><th>商品</th><th>项目</th><th>套图完成度</th><th>当前状态</th><th>操作</th></tr></thead><tbody>{rows.map(({ project, sku }) => { const done = sku.outputs.filter((output) => output.status === "completed").length; const active = sku.outputs.find((output) => output.status === "running" || output.status === "failed" || output.status === "queued"); return <tr key={sku.id}><td><Link className="font-medium text-slate-950" to={`/projects/${project.id}/studio/${sku.id}`}>{sku.name}</Link></td><td>{project.name}</td><td>{done} / {sku.outputs.length || "—"}</td><td><span className={`status status-${active?.status ?? "draft"}`}>{statusText(active?.status ?? "draft")}</span>{active?.failureReason && <span className="ml-2 text-xs text-rose-600">{active.failureReason}</span>}</td><td><Link className="text-sm font-semibold text-indigo-700" to={`/projects/${project.id}/studio/${sku.id}`}>处理商品</Link></td></tr>; })}</tbody></table></div></section></Shell>;
}

function Review() {
  const { workspace, updateReview } = useWorkspace();
  const candidates = workspace.projects.flatMap((project) => project.skus.flatMap((sku) => sku.outputs.filter((output) => output.status === "completed" && output.reviewStatus !== "accepted").map((output) => ({ project, sku, output }))));
  const [selectedId, setSelectedId] = useState(candidates[0]?.output.id ?? "");
  const [marks, setMarks] = useState<Array<{ x: number; y: number }>>([]);
  const [message, setMessage] = useState("");
  const selected = candidates.find((item) => item.output.id === selectedId) ?? candidates[0];
  if (!selected) return <Shell><PageHeading eyebrow="人工审核" title="审核中心" /><EmptyState title="没有待审核结果" description="生成完成的商品图会在这里等待确认。" /></Shell>;
  const requestChange = () => { updateReview(selected.project.id, selected.sku.id, selected.output.id, "changes_requested"); setMessage("已标记为需要修改"); };
  const accept = () => { updateReview(selected.project.id, selected.sku.id, selected.output.id, "accepted"); setMessage("已通过审核"); };
  const mark = (event: React.MouseEvent<HTMLButtonElement>) => { const rect = event.currentTarget.getBoundingClientRect(); setMarks((current) => [...current, { x: ((event.clientX - rect.left) / (rect.width || 1)) * 100, y: ((event.clientY - rect.top) / (rect.height || 1)) * 100 }]); };
  return <Shell><PageHeading eyebrow="人工审核" title="审核中心" /><div className="grid gap-6 xl:grid-cols-[270px_minmax(0,1fr)_300px]"><aside className="surface p-3"><p className="section-label px-2 py-2">待判断输出图</p>{candidates.map((item) => <button key={item.output.id} onClick={() => { setSelectedId(item.output.id); setMarks([]); setMessage(""); }} className={`review-item ${selected.output.id === item.output.id ? "review-item-active" : ""}`}><span>{item.sku.name}</span><small>{item.output.slot} · v{item.output.version}</small></button>)}</aside><section className="surface p-5"><div className="flex items-center justify-between"><div><p className="section-label">点击结果图圈选问题</p><h2 className="mt-1 font-semibold">{selected.sku.name} · {selected.output.name}</h2></div><span className="status status-completed">待审核</span></div><button aria-label="圈选图片问题" onClick={mark} className="review-canvas mt-5">{selected.output.imageUrl ? <img src={selected.output.imageUrl} alt="待审核结果" /> : <span>结果图预览</span>}{marks.map((point, index) => <i key={`${point.x}-${point.y}`} className="review-mark" style={{ left: `${point.x}%`, top: `${point.y}%` }}>{index + 1}</i>)}</button></section><aside className="surface p-5"><p className="section-label">审核结论</p><p className="mt-2 text-sm text-slate-500">圈选内容会随“请求修改”一起成为下一版的修改指令。</p><textarea className="mt-4" placeholder="说明需要调整的地方" /><div className="mt-4 grid grid-cols-2 gap-2"><button className="secondary-button justify-center" onClick={requestChange}>请求修改</button><button className="primary-button justify-center" onClick={accept}>通过</button></div>{message && <p className="mt-4 text-sm font-medium text-emerald-700">{message}</p>}</aside></div></Shell>;
}

function EmptyState({ title, description }: { title: string; description: string }) { return <section className="surface grid min-h-56 place-items-center p-8 text-center"><div><h2 className="font-semibold">{title}</h2><p className="mt-2 text-sm text-slate-500">{description}</p></div></section>; }
function NotFound() { return <Shell><PageHeading eyebrow="未找到内容" title="这个页面暂不可用" action={<Link className="primary-button" to="/">返回工作台</Link>} /></Shell>; }
function statusText(status: GenerationStatus) { return ({ draft: "待配置", queued: "排队中", running: "生成中", completed: "已完成", failed: "需处理" })[status]; }

function AppRoutes() { return <Routes><Route path="/" element={<Dashboard />} /><Route path="/projects/new" element={<ProjectNew />} /><Route path="/projects/:projectId" element={<ProjectGrouping />} /><Route path="/projects/:projectId/studio/:skuId" element={<Studio />} /><Route path="/production" element={<Production />} /><Route path="/review" element={<Review />} /><Route path="*" element={<NotFound />} /></Routes>; }

export default function App() { return <WorkspaceProvider><AppRoutes /></WorkspaceProvider>; }
