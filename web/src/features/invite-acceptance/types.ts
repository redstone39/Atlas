import type { MessageReference } from "../../shared/user-messages";

export interface InviteAcceptResult extends MessageReference {
  request_id: string;
  status: "applied" | "rejected";
  target_ref: string | null;
  audit_event_ref: string;
}
