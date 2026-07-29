import { developmentWorkspace } from "./mock-data";
import type { PreflightResult, Project, ProjectInput, ReviewInput, WorkspaceSnapshot } from "./types";

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
  headers?: { get(name: string): string | null };
  json: () => Promise<unknown>;
};

function isLoginResponse(response: JsonResponse) {
  return response.redirected || response.headers?.get("content-type")?.toLowerCase().includes("text/html");
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

export async function uploadAssets(projectId: string, files: File[]) {
  const body = new FormData();
  files.forEach((file) => {
    const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
    body.append("files", file, relativePath || file.name);
  });
  const headers = new Headers({ "X-CSRFToken": await csrfToken() });
  const response = await fetch(`/api/projects/${projectId}/assets/`, { method: "POST", body, headers, credentials: "same-origin" });
  return jsonFor<{ asset_count: number }>(response);
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

export function preflightProject(projectId: string) {
  return jsonRequest<PreflightResult>(`/api/projects/${projectId}/preflight/`);
}

export function retryGeneration(generationId: string) {
  return jsonRequest(`/api/generations/${generationId}/retry/`, { method: "POST", body: "{}" });
}

export function submitReview(generationId: string, input: ReviewInput) {
  return jsonRequest(`/api/generations/${generationId}/review/`, { method: "POST", body: JSON.stringify(input) });
}

export async function exportProject(projectId: string) {
  const response = await fetch(`/api/projects/${projectId}/export/`, { credentials: "same-origin" });
  if (!response.ok || isLoginResponse(response)) throw await errorFor(response);
  return response.blob();
}
