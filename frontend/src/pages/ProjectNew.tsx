import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { createProject } from "../api";
import { ErrorPanel, PageHeading, Shell } from "../layout";
import type { ProjectInput } from "../types";

export default function ProjectNew() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [input, setInput] = useState<ProjectInput>({
    name: "未命名出图项目",
    platform: "shopee",
    market: "SG",
    seller_tier: "general",
    template: "",
    size: "1:1",
    resolution: "1k",
    global_prompt: "",
  });
  const create = useMutation({ mutationFn: createProject, onSuccess: async (project) => { await queryClient.invalidateQueries({ queryKey: ["workspace"] }); navigate(`/projects/${project.id}`); } });
  return <Shell><PageHeading eyebrow="新建项目" title="创建出图项目" /><form onSubmit={(event) => { event.preventDefault(); create.mutate(input); }} className="mx-auto max-w-3xl space-y-6"><section className="surface p-6"><div className="grid gap-5 sm:grid-cols-2"><Field label="项目名称"><input id="project-name" value={input.name} onChange={(event) => setInput({ ...input, name: event.target.value })} /></Field><Field label="平台"><select value={input.platform} onChange={(event) => setInput({ ...input, platform: event.target.value, seller_tier: "general" })}><option value="shopee">Shopee</option><option value="tiktok">TikTok Shop</option></select></Field><Field label="市场"><input value={input.market} list="market-suggestions" autoCapitalize="characters" onChange={(event) => setInput({ ...input, market: event.target.value.toUpperCase() })} /><datalist id="market-suggestions"><option value="SG" /><option value="MY" /><option value="TH" /><option value="VN" /><option value="PH" /><option value="ID" /><option value="TW" /><option value="BR" /><option value="US" /></datalist></Field>{input.platform === "shopee" && <Field label="店铺类型"><select value={input.seller_tier} onChange={(event) => setInput({ ...input, seller_tier: event.target.value as "general" | "mall" })}><option value="general">普通店</option><option value="mall">Mall</option></select></Field>}<Field label="套图模板"><input value={input.template} placeholder="留空时自动选择可用规则模板" onChange={(event) => setInput({ ...input, template: event.target.value })} /></Field><Field label="比例"><select value={input.size} onChange={(event) => setInput({ ...input, size: event.target.value })}><option>1:1</option><option>3:4</option></select></Field><Field label="分辨率"><select value={input.resolution} onChange={(event) => setInput({ ...input, resolution: event.target.value })}><option>1k</option><option>2k</option></select></Field></div><p className="mt-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"><strong>规则基线</strong>：已核实市场自动加载官方规则；其他市场使用内部通用模板并在人工审核中明确标记，不宣称官方自动合规。</p><Field label="全局出图要求"><textarea value={input.global_prompt} onChange={(event) => setInput({ ...input, global_prompt: event.target.value })} /></Field></section>{create.isError && <ErrorPanel error={create.error} retry={() => create.mutate(input)} />}<button className="primary-button" disabled={create.isPending} type="submit">{create.isPending ? "正在创建…" : "创建项目并上传素材"}</button></form></Shell>;
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="block text-sm font-medium text-slate-700"><span className="mb-2 block">{label}</span>{children}</label>; }
