export type GenerationStatus = "draft" | "queued" | "running" | "completed" | "failed";
export type ReviewStatus = "pending" | "accepted" | "changes_requested" | "rejected";
export type ReviewDecision = "accept" | "changes_requested";
export type ImportMode = "auto" | "organize";
export type RelationType = "single_product" | "same_product" | "variant_group";

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
  slotId: string;
  slotOrder: number;
  attempt: number;
  version: number;
  status: GenerationStatus;
  reviewStatus: ReviewStatus;
  imageUrl?: string;
  failureReason?: string;
  prompt?: string;
}

export interface ProductPrompt {
  slotOrder: number;
  slot: string;
  text: string;
}

export interface ProductSku {
  id: string;
  name: string;
  sku?: string;
  relationType?: RelationType;
  assetIds: string[];
  assets?: ProductAsset[];
  facts: string;
  identityLock: string;
  brief: string;
  preparationStatus?: string;
  version: number;
  prompts?: ProductPrompt[];
  outputs: OutputImage[];
}

export interface Project {
  id: string;
  name: string;
  platform: string;
  market: string;
  template: string;
  size: string;
  resolution?: string;
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

export interface RevisionInput {
  issue_tags: string[];
  description: string;
  annotations: ReviewAnnotation[];
}

export interface PreflightResult {
  cluster_count: number;
  slot_count: number;
  generation_count: number;
  blocking_errors: string[];
}
