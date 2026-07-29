export type GenerationStatus = "draft" | "queued" | "running" | "completed" | "failed";
export type ReviewStatus = "pending" | "accepted" | "changes_requested";

export interface ProductAsset {
  id: string;
  name: string;
  imageUrl?: string;
  kind?: "image" | "txt";
}

export interface OutputImage {
  id: string;
  name: string;
  slot: string;
  status: GenerationStatus;
  reviewStatus: ReviewStatus;
  version: number;
  imageUrl?: string;
  failureReason?: string;
}

export interface ProductSku {
  id: string;
  name: string;
  assetIds: string[];
  facts: string;
  identityLock: string;
  brief: string;
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
  updatedAt?: string;
}

export interface ProjectInput {
  name: string;
  platform: string;
  market: string;
  template: string;
  size: string;
}
