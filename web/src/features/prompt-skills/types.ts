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
