import { developmentWorkspace } from "./mock-data";
import type { ImportMode, PreflightResult, Project, ProjectInput, ReviewInput, RevisionInput, WorkspaceSnapshot } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public authRequired = false) {
    super(message);
    this.name = "ApiError";
  }
}

function demoMode() {
  return import.meta.env.VITE_DEMO_MODE === "true";
}

type JsonResponse = {
  ok: boolean;
  status: number;
  redirected?: boolean;
  url?: string;
  headers?: { get(name: string): string | null };
  json: () => Promise<unknown>;
};

function isAuthenticationUrl(url: string | undefined) {
  if (!url) return false;
  try {
    const path = new URL(url, window.location.origin).pathname.toLowerCase();
    return /(?:^|\/)(?:login|password)(?:\/|$)/.test(path);
  } catch {
    return false;
  }
}

function isLoginResponse(response: JsonResponse) {
  return Boolean(response.redirected) || isAuthenticationUrl(response.url);
}

async function errorFor(response: JsonResponse) {
  if (isLoginResponse(response)) return new ApiError(401, "登录已失效或需修改密码", true);
  let message = `请求失败（${response.status}）`;
  try {
    const body = await response.json() as { error?: string; message?: string };
    message = body.error ?? body.message ?? message;
  } catch {
    // Keep the status-based message when the server did not return JSON.
  }
  return new ApiError(response.status, message, response.status === 401);
}

async function jsonFor<T>(response: JsonResponse): Promise<T> {
  if (!response.ok || isLoginResponse(response)) throw await errorFor(response);
  return response.json() as Promise<T>;
}

async function csrfToken() {
  const response = await fetch("/api/csrf/", { credentials: "same-origin" });
  const payload = await jsonFor<{ csrf_token?: string }>(response);
  if (!payload.csrf_token) throw new ApiError(500, "CSRF 初始化响应无效");
  return payload.csrf_token;
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.method && init.method !== "GET") {
    headers.set("Content-Type", "application/json");
    headers.set("X-CSRFToken", await csrfToken());
  }
  const response = await fetch(url, { ...init, headers, credentials: "same-origin" });
  return jsonFor<T>(response);
}

export async function loadWorkspace(): Promise<WorkspaceSnapshot> {
  try {
    return await jsonRequest<WorkspaceSnapshot>("/api/workspace/snapshot/");
  } catch (error) {
    if (demoMode()) return developmentWorkspace;
    throw error;
  }
}

export function loadProject(projectId: string) {
  return jsonRequest<Project>(`/api/projects/${projectId}/snapshot/`);
}

export async function createProject(input: ProjectInput): Promise<Project> {
  const payload = { ...input, market: input.market.trim().toUpperCase() };
  try {
    return await jsonRequest<Project>("/api/projects/", { method: "POST", body: JSON.stringify(payload) });
  } catch (error) {
    if (!demoMode()) throw error;
    return {
      id: `demo-project-${crypto.randomUUID()}`,
      ...payload,
      status: "draft",
      assets: [],
      skus: [],
      updatedAt: new Date().toISOString(),
    };
  }
}

export async function uploadAssets(projectId: string, files: File[], mode: ImportMode = "organize") {
  const body = new FormData();
  body.append("mode", mode);
  files.forEach((file) => {
    const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
    body.append("files", file, relativePath || file.name);
    body.append("relative_paths", relativePath || file.name);
  });
  const headers = new Headers({ "X-CSRFToken": await csrfToken() });
  const response = await fetch(`/api/projects/${projectId}/assets/`, { method: "POST", body, headers, credentials: "same-origin" });
  return jsonFor<{ asset_count: number }>(response);
}

export function importSkus(projectId: string, skus: string[], mode: ImportMode) {
  return jsonRequest(`/api/projects/${projectId}/sku-import/`, {
    method: "POST",
    body: JSON.stringify({ skus, mode }),
  });
}

export function updateCluster(clusterId: string, expectedVersion: number, payload: Record<string, string>) {
  return jsonRequest(`/api/clusters/${clusterId}/`, {
    method: "POST",
    body: JSON.stringify({ expected_version: expectedVersion, ...payload }),
  });
}

export function mergeAsset(clusterId: string, assetId: string, expectedVersion: number) {
  return jsonRequest(`/api/clusters/${clusterId}/merge/`, {
    method: "POST",
    body: JSON.stringify({ asset_id: assetId, expected_version: expectedVersion }),
  });
}

export function confirmProject(projectId: string) {
  return jsonRequest(`/api/projects/${projectId}/confirm/`, { method: "POST", body: "{}" });
}

export function generateProject(projectId: string, input: { clusterIds: string[]; slotOrders: number[] }) {
  return jsonRequest(`/api/projects/${projectId}/generate/`, {
    method: "POST",
    body: JSON.stringify({ cluster_ids: input.clusterIds, slot_orders: input.slotOrders }),
  });
}

export async function preflightProject(projectId: string): Promise<PreflightResult> {
  const result = await jsonRequest<PreflightResult>(`/api/projects/${projectId}/preflight/`, { method: "POST", body: "{}" });
  return {
    cluster_count: result.cluster_count,
    slot_count: result.slot_count,
    generation_count: result.generation_count,
    blocking_errors: result.blocking_errors,
  };
}

export function retryGeneration(generationId: string) {
  return jsonRequest(`/api/generations/${generationId}/retry/`, { method: "POST", body: "{}" });
}

export function regenerateGeneration(generationId: string) {
  return jsonRequest(`/api/generations/${generationId}/regenerate/`, { method: "POST", body: "{}" });
}

export function submitReview(generationId: string, input: ReviewInput) {
  return jsonRequest(`/api/generations/${generationId}/review/`, { method: "POST", body: JSON.stringify(input) });
}

export function reviseGeneration(generationId: string, input: RevisionInput) {
  return jsonRequest(`/api/generations/${generationId}/revise/`, { method: "POST", body: JSON.stringify(input) });
}

export async function exportProject(projectId: string, generationIds: string[] = []) {
  const headers = new Headers({ "Content-Type": "application/json", "X-CSRFToken": await csrfToken() });
  const response = await fetch(`/api/projects/${projectId}/export/`, {
    method: "POST",
    body: JSON.stringify({ generation_ids: generationIds }),
    headers,
    credentials: "same-origin",
  });
  if (!response.ok || isLoginResponse(response)) throw await errorFor(response);
  return response.blob();
}
