import { AgentResearchAuditFeature } from "../../features/agent-research-audit";
import { ConversationAuditFeature } from "../../features/conversation-audit/index";
import { matchAppRoute, type AppRoute } from "../../shared/routes";

export function AuditPage({
  route,
  onNavigate,
}: {
  route: AppRoute;
  onNavigate: (route: AppRoute) => void;
}) {
  if (matchAppRoute(route).kind === "admin-audit-agent-research") {
    return <AgentResearchAuditFeature route={route} onNavigate={onNavigate} />;
  }
  return <ConversationAuditFeature route={route} onNavigate={onNavigate} />;
}
