import { ConversationAuditFeature } from "../../features/conversation-audit/index";
import type { AppRoute } from "../../shared/routes";

export function AuditPage({
  route,
  onNavigate,
}: {
  route: AppRoute;
  onNavigate: (route: AppRoute) => void;
}) {
  return <ConversationAuditFeature route={route} onNavigate={onNavigate} />;
}
