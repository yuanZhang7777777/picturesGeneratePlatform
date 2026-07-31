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
  readOnly?: boolean;
}

export type PromptFactClass = "confirmed" | "observed" | "inferred";

export interface PromptFact {
  fact_id: string;
  statement: string;
  fact_class: PromptFactClass;
  confidence: number;
  evidence_refs: string[];
  risk_level: string;
  allowed_uses: string[];
  review_note?: string;
}

export interface FactLedgerSnapshot {
  facts?: PromptFact[];
  review_summary?: {
    confirmed_count: number;
    observed_count: number;
    inferred_count: number;
    high_risk_count: number;
  };
  blocked_claim_topics?: string[];
}

export type RuleGateMessage = string | {
  message?: string;
  reason?: string;
  statement?: string;
  rule_id?: string;
};

export interface RuleGateSnapshot {
  decision?: "pass" | "block";
  hard_blocks?: RuleGateMessage[];
  semantic_risks?: RuleGateMessage[];
  warnings?: RuleGateMessage[];
}

export interface PromptAnalysisSnapshot {
  fact_ledger?: FactLedgerSnapshot;
  rule_gate?: RuleGateSnapshot;
  readiness?: { status?: string; code?: string; required_fields?: string[] };
}

export interface SkuImportItem {
  sku: string;
  productName?: string;
  status: "imported" | "failed";
  clusterId?: string | null;
  errorCode?: string | null;
}

export interface SkuImportResult {
  imported: number;
  failed: number;
  items: SkuImportItem[];
}

export interface ClusterUpdateInput {
  name?: string;
  relation_type?: RelationType;
  identity_lock?: string;
  prompt_override?: string;
  platform_override?: string | null;
  market_override?: string | null;
  seller_tier_override?: "general" | "mall" | null;
  prompts?: { slot_order: number; prompt: string }[];
}

export interface ProductConfiguration {
  platform: string;
  market: string;
  sellerTier: "general" | "mall";
  size: string;
  resolution: string;
  globalPrompt: string;
}

export interface ProductOverrides {
  platform: string | null;
  market: string | null;
  sellerTier: "general" | "mall" | null;
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
  overrides?: ProductOverrides;
  effectiveConfig?: ProductConfiguration;
  version: number;
  prompts?: ProductPrompt[];
  analysisSnapshot?: PromptAnalysisSnapshot;
  outputs: OutputImage[];
}

export interface Project {
  id: string;
  name: string;
  platform: string;
  market: string;
  sellerTier?: "general" | "mall";
  configurationStatus?: "required" | "configured";
  defaultConfig?: ProductConfiguration;
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
  platform?: string;
  market?: string;
  seller_tier?: "general" | "mall";
  template?: string;
  size?: string;
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
