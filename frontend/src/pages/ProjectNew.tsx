import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { createProject } from "../api";
import { ErrorPanel, PageHeading, Shell } from "../layout";
import type { ProjectInput } from "../types";

export default function ProjectNew() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [input, setInput] = useState<ProjectInput>({ name: "未命名出图项目", platform: "shopee", market: "SG", template: "", size: "1:1", resolution: "1k", global_prompt: "" });
  const create = useMutation({ mutationFn: createProject, onSuccess: async (project) => { await queryClient.invalidateQueries({ queryKey: ["workspace"] }); navigate(`/projects/${project.id}`); } });
  return <Shell><PageHeading eyebrow="新建项目" title="创建出图项目" /><form onSubmit={(event) => { event.preventDefault(); create.mutate(input); }} className="mx-auto max-w-3xl space-y-6"><section className="surface p-6"><div className="grid gap-5 sm:grid-cols-2"><Field label="项目名称"><input id="project-name" value={input.name} onChange={(event) => setInput({ ...input, name: event.target.value })} /></Field><Field label="平台"><select value={input.platform} onChange={(event) => setInput({ ...input, platform: event.target.value })}><option value="shopee">Shopee</option><option value="tiktok">TikTok Shop</option></select></Field><Field label="市场"><input value={input.market} list="market-suggestions" autoCapitalize="characters" onChange={(event) => setInput({ ...input, market: event.target.value.toUpperCase() })} /><datalist id="market-suggestions"><option value="SG" /><option value="MY" /><option value="TH" /><option value="VN" /><option value="PH" /><option value="ID" /><option value="TW" /><option value="BR" /><option value="US" /></datalist></Field><Field label="套图模板"><input value={input.template} placeholder="留空时使用平台默认模板" onChange={(event) => setInput({ ...input, template: event.target.value })} /></Field><Field label="比例"><select value={input.size} onChange={(event) => setInput({ ...input, size: event.target.value })}><option>1:1</option><option>3:4</option></select></Field><Field label="分辨率"><select value={input.resolution} onChange={(event) => setInput({ ...input, resolution: event.target.value })}><option>1k</option><option>2k</option></select></Field></div><p className="mt-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"><strong>通用基线</strong>：市场代码可自由输入；<strong>市场专属规则待确认</strong>，仅在规则来源与版本通过预检后才会纳入生成。</p><Field label="全局出图要求"><textarea value={input.global_prompt} onChange={(event) => setInput({ ...input, global_prompt: event.target.value })} /></Field></section>{create.isError && <ErrorPanel error={create.error} retry={() => create.mutate(input)} />}<button className="primary-button" disabled={create.isPending} type="submit">{create.isPending ? "正在创建…" : "创建项目并上传素材"}</button></form></Shell>;
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="block text-sm font-medium text-slate-700"><span className="mb-2 block">{label}</span>{children}</label>; }
