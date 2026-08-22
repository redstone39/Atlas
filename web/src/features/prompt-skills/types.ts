export type PromptSkillCategory = "understanding" | "planner" | "answer";

export interface PromptSkillRef {
  category: PromptSkillCategory;
  name: string;
  revision: number;
  content_digest: string;
}

export interface PromptSkillRevision {
  ref: PromptSkillRef;
  description: string;
  license: string | null;
  compatibility: string | null;
  metadata: Record<string, string>;
  created_by: string;
  created_at: string;
  enabled: boolean;
  source: string | null;
  instructions: string | null;
}

export interface PromptSkillControl {
  category: PromptSkillCategory;
  name: string;
  head_revision: number;
  enabled_revision: number | null;
  control_revision: number;
}

export interface PromptSkillSummary {
  control: PromptSkillControl;
  head: PromptSkillRevision;
  revisions: PromptSkillRevision[];
}

export interface PromptSkillList {
  items: PromptSkillSummary[];
}

export interface PromptSkillMutationOutcome {
  skill: PromptSkillSummary;
  revision: PromptSkillRevision | null;
  replayed: boolean;
}

export type SkillCandidateStatus =
  | "draft"
  | "applying"
  | "stale"
  | "approved"
  | "rejected";

export interface SkillCandidateSummary {
  candidate_ref: string;
  draft_key: string;
  disposition: "add" | "revise";
  category: PromptSkillCategory;
  target_name: string;
  topic: string;
  goal: string;
  draft_revision: number;
  status: SkillCandidateStatus;
  skill_source_digest: string;
  updated_at: string;
}

export interface SkillCandidateDetail extends SkillCandidateSummary {
  source_evidence: Array<{
    consolidation_ref: string;
    consolidation_digest: string;
    generalized_experience_ordinal: number;
  }>;
  observed_catalog_refs: Array<{
    category: PromptSkillCategory;
    catalog_revision: number;
    catalog_digest: string;
  }>;
  matched_skill_refs: PromptSkillRef[];
  skill_source: string;
  rationale: string;
  risk: string;
  approved_skill_ref: PromptSkillRef | null;
}

export interface SkillCandidateList {
  items: SkillCandidateSummary[];
}

export interface SkillCandidateMutationOutcome {
  candidate_ref: string;
  draft_revision: number;
  status: SkillCandidateStatus;
  outcome: "approved" | "rejected" | "stale" | "replayed" | "conflict";
  approved_skill_ref: PromptSkillRef | null;
}
