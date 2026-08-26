import { requestJson } from "../../shared/api-client";
import type { DeclaredEvidencePreview } from "../workspace";
import type {
  AgentResearchAuditDetail,
  AgentResearchAuditList,
  AgentResearchEvidenceContent,
  AgentResearchRuntimeDetail,
  ResearchEvidenceDescriptor,
} from "./types";

const basePath = "/api/v1/admin/audit/agent-research";

function decodeBase64(value: string): ArrayBuffer {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer;
}

export const agentResearchAuditApi = {
  list: (cursor?: string) => requestJson<AgentResearchAuditList>(
    cursor ? `${basePath}?cursor=${encodeURIComponent(cursor)}` : basePath,
  ),
  detail: (researchId: string) => requestJson<AgentResearchAuditDetail>(
    `${basePath}/${encodeURIComponent(researchId)}`,
  ),
  runtime: (researchId: string) => requestJson<AgentResearchRuntimeDetail>(
    `${basePath}/${encodeURIComponent(researchId)}/runtime`,
  ),
  evidence: async (
    researchId: string,
    descriptor: ResearchEvidenceDescriptor,
    representation: "text" | "visual" | "native",
  ): Promise<DeclaredEvidencePreview> => {
    const result = await requestJson<AgentResearchEvidenceContent>(
      `${basePath}/${encodeURIComponent(researchId)}/evidence/${encodeURIComponent(descriptor.evidence_id)}?representation=${representation}`,
    );
    if (result.representation === "text" && result.text !== null) {
      return {
        kind: "excerpt",
        evidence: {
          evidence_handle: descriptor.evidence_id,
          locator_label: descriptor.locator,
          snippet: "",
          content: result.text,
          modality: "text",
        },
      };
    }
    if (result.content_base64 === null || result.media_type === "text/plain") {
      throw new Error("evidence_not_available");
    }
    return {
      kind: "page",
      mediaType: result.media_type,
      blob: new Blob([decodeBase64(result.content_base64)], {
        type: result.media_type,
      }),
    };
  },
};
