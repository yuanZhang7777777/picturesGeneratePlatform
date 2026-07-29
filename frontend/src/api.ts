import { developmentWorkspace } from "./mock-data";
import type { Project, ProjectInput, WorkspaceSnapshot } from "./types";

let csrfTokenRequest: Promise<string> | undefined;

async function csrfToken() {
  csrfTokenRequest ??= fetch("/api/csrf/", { credentials: "same-origin" })
    .then(async (response) => {
      if (!response.ok) throw new Error(`CSRF bootstrap failed: ${response.status}`);
      const payload = await response.json() as { csrf_token?: string; csrfToken?: string };
      const token = payload.csrf_token ?? payload.csrfToken;
      if (!token) throw new Error("CSRF bootstrap returned no token");
      return token;
    })
    .catch((error) => {
      csrfTokenRequest = undefined;
      throw error;
    });
  return csrfTokenRequest;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.method && init.method !== "GET") {
    headers.set("Content-Type", "application/json");
    headers.set("X-CSRFToken", await csrfToken());
  }
  const response = await fetch(url, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

interface BatchSnapshot {
  batch: { id: string; name: string; platform: string; site: string; status: string };
  clusters: Array<{
    id: string;
    name: string;
    assets?: Array<{ id: string; filename: string }>;
    generations?: Array<{ id: string; slot: string; status: string; review_status: string; attempt: number }>;
  }>;
}

function projectFromBatchSnapshot(snapshot: BatchSnapshot): Project {
  return {
    id: snapshot.batch.id,
    name: snapshot.batch.name,
    platform: snapshot.batch.platform,
    market: snapshot.batch.site,
    template: "待后端模板快照",
    size: "待后端尺寸快照",
    status: snapshot.batch.status === "completed" ? "completed" : "running",
    updatedAt: "刚刚",
    assets: snapshot.clusters.flatMap((cluster) =>
      (cluster.assets ?? []).map((asset) => ({ id: asset.id, name: asset.filename })),
    ),
    skus: snapshot.clusters.map((cluster) => ({
      id: cluster.id,
      name: cluster.name,
      assetIds: (cluster.assets ?? []).map((asset) => asset.id),
      facts: "",
      identityLock: "",
      brief: "",
      outputs: (cluster.generations ?? []).map((generation) => ({
        id: generation.id,
        name: generation.slot,
        slot: generation.slot,
        status: generation.status === "completed" ? "completed" : generation.status === "failed" ? "failed" : "queued",
        reviewStatus: generation.review_status === "accepted" ? "accepted" : generation.review_status === "rejected" ? "changes_requested" : "pending",
        version: generation.attempt,
      })),
    })),
  };
}

export async function loadWorkspace(): Promise<WorkspaceSnapshot> {
  try {
    const data = await request<WorkspaceSnapshot | BatchSnapshot>("/api/workspace/snapshot/");
    if ("projects" in data) return data;
    return { projects: [projectFromBatchSnapshot(data)] };
  } catch {
    return developmentWorkspace;
  }
}

export async function createProject(input: ProjectInput): Promise<Project> {
  try {
    return await request<Project>("/api/projects/", { method: "POST", body: JSON.stringify(input) });
  } catch {
    return {
      id: `project-local-${crypto.randomUUID()}`,
      ...input,
      status: "draft",
      assets: [],
      skus: [],
      updatedAt: "本地草稿",
    };
  }
}

export async function uploadAssets(projectId: string, files: File[]) {
  const body = new FormData();
  files.forEach((file) => body.append("files", file, file.name));
  const headers = new Headers();
  headers.set("X-CSRFToken", await csrfToken());
  const response = await fetch(`/api/projects/${projectId}/assets/`, {
    method: "POST",
    body,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(`Upload failed: ${response.status}`);
  return response.json() as Promise<{ asset_count: number }>;
}
