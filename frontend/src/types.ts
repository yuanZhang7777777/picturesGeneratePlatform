export type GenerationStatus = "draft" | "queued" | "running" | "completed" | "failed";
export type ReviewStatus = "pending" | "accepted" | "changes_requested" | "rejected";
export type ReviewDecision = "accept" | "changes_requested";

export interface ProductAsset {
  id: string;
  name: string;
  imageUrl?: string;
  kind: "image" | "txt";
}

export interface OutputImage {
  id: string;
  name: string;
  slot: string;
  attempt: number;
  version: number;
  status: GenerationStatus;
  reviewStatus: ReviewStatus;
  imageUrl?: string;
  failureReason?: string;
}

export interface ProductSku {
  id: string;
  name: string;
  assetIds: string[];
  assets?: ProductAsset[];
  facts: string;
  identityLock: string;
  brief: string;
  version: number;
  outputs: OutputImage[];
}

export interface Project {
  id: string;
  name: string;
  platform: string;
  market: string;
  template: string;
  size: string;
  status: GenerationStatus;
  assets: ProductAsset[];
  skus: ProductSku[];
  updatedAt: string;
}

export interface WorkspaceSnapshot {
  projects: Project[];
}

export interface ProjectInput {
  name: string;
  platform: string;
  market: string;
  template: string;
  size: string;
  resolution?: string;
  global_prompt?: string;
}

export interface ReviewAnnotation {
  kind: "circle" | "stroke";
  points?: number[][];
  rect?: [number, number, number, number];
  color: string;
  width: number;
}

export interface ReviewInput {
  decision: ReviewDecision;
  issue_tags: string[];
  description: string;
  annotations: ReviewAnnotation[];
}
