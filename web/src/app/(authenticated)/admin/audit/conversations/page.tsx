import { adminAuditSectionRoute } from "@/shared/routes";
import { AuditConversationsScreen } from "./screen";

export default function AuditConversationsRoute() {
  return <AuditConversationsScreen route={adminAuditSectionRoute("conversations")} />;
}
