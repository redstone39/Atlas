import { requestJson } from "../../shared/api-client";
import type {
  PromptSkillCategory,
  PromptSkillList,
  PromptSkillMutationOutcome,
  PromptSkillRevision,
  PromptSkillSummary,
} from "./types";

const skillPath = (category: PromptSkillCategory, name: string) =>
  `/api/v1/admin/prompt-skills/${category}/${encodeURIComponent(name)}`;

export const promptSkillsApi = {
  list: (category: PromptSkillCategory) =>
    requestJson<PromptSkillList>(
      `/api/v1/admin/prompt-skills?category=${encodeURIComponent(category)}`,
    ),

  getRevision: (
    category: PromptSkillCategory,
    name: string,
    revision: number,
  ) =>
    requestJson<PromptSkillRevision>(
      `${skillPath(category, name)}/revisions/${revision}`,
    ),

  upload: (
    category: PromptSkillCategory,
    name: string,
    file: File,
    expectedHeadRevision: number,
    idempotencyKey: string,
  ) => {
    const body = new FormData();
    body.set("file", file);
    return requestJson<PromptSkillMutationOutcome>(
      `${skillPath(category, name)}/revisions`,
      {
        method: "POST",
        body,
        headers: {
          "Idempotency-Key": idempotencyKey,
          "If-Match": String(expectedHeadRevision),
        },
      },
    );
  },

  setEnabled: (
    category: PromptSkillCategory,
    skill: PromptSkillSummary,
    revision: number,
    enabled: boolean,
  ) => {
    const key = crypto.randomUUID();
    const expectedControlRevision = skill.control.control_revision;
    return requestJson<PromptSkillMutationOutcome>(
      `${skillPath(category, skill.control.name)}/revisions/${revision}/${enabled ? "enable" : "disable"}`,
      {
        method: "POST",
        headers: {
          "Idempotency-Key": key,
          "If-Match": String(expectedControlRevision),
        },
        body: JSON.stringify({
          expected_control_revision: expectedControlRevision,
          idempotency_key: key,
        }),
      },
    );
  },
};
