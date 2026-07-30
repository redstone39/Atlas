import { requestJson } from "../../shared/api-client";
import { conversationDetail, conversationSummary } from "../workspace";
import type { ConversationDetail, ProtectedDeclaredEvidenceDto } from "../workspace";
import type {
  AdminConversationDetailDto,
  AdminConversationListDto,
  AdminConversationListResult,
  AuditEventList,
  RuntimeTraceDetail,
} from "./types";

export const conversationAuditApi = {
  listAuditEvents: () =>
    requestJson<AuditEventList>("/api/v1/admin/audit/events"),
  listAdminConversations: async (cursor?: string): Promise<AdminConversationListResult> => {
    const result = await requestJson<AdminConversationListDto>(
      cursor
        ? `/api/v1/admin/conversations?cursor=${encodeURIComponent(cursor)}`
        : "/api/v1/admin/conversations",
    );
    return {
      conversations: result.conversations.map(conversationSummary),
      next_cursor: result.next_cursor,
    };
  },
  getAdminConversation: async (conversationId: string): Promise<ConversationDetail> =>
    conversationDetail(await requestJson<AdminConversationDetailDto>(
      `/api/v1/admin/conversations/${encodeURIComponent(conversationId)}`,
    )),
  getAdminConversationRuntime: (conversationId: string, turnId: string) =>
    requestJson<RuntimeTraceDetail>(
      `/api/v1/admin/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/runtime`,
    ),
  readAdminDeclaredEvidence: (
    conversationId: string,
    turnId: string,
    protectedOpenRef: string,
  ) => requestJson<ProtectedDeclaredEvidenceDto>(
    `/api/v1/admin/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/declared-evidence/${encodeURIComponent(protectedOpenRef)}`,
  ),
};
