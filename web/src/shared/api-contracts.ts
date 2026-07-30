import type { MessageReference } from "./user-messages";

export interface AdminActionResult extends MessageReference {
  request_id: string;
  status: "applied" | "rejected" | "access_denied";
  target_ref: string | null;
  audit_event_ref: string;
}
