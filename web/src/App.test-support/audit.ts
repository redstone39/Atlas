import { auditEvents } from "../App.test-agent-fixtures";

import type { MockApiHandler } from "./protocol";
import { jsonResponse } from "./protocol";
import {
  adminDetailDto,
  conversationSummaries,
  runtimeTraceDetail,
} from "./workspace";

export function createAuditHandler(): MockApiHandler {
  return ({ url, method }) => {
    if (url.pathname === "/api/v1/admin/audit/events") {
      return jsonResponse(auditEvents);
    }
    if (url.pathname === "/api/v1/admin/conversations" && method === "GET") {
      return jsonResponse({
        conversations: conversationSummaries.map(({ last_turn_status: _status, ...item }) => item),
        next_cursor: null,
      });
    }
    if (url.pathname === "/api/v1/admin/conversations/conv-supported-001" && method === "GET") {
      return jsonResponse(adminDetailDto());
    }
    if (
      url.pathname === "/api/v1/admin/conversations/conv-supported-001/turns/turn-answer-001/runtime" &&
      method === "GET"
    ) {
      return jsonResponse(runtimeTraceDetail);
    }
    return undefined;
  };
}
