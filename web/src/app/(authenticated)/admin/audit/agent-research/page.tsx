import { adminAgentResearchAuditRoute } from "@/shared/routes";
import { AgentResearchAuditScreen } from "./screen";

export default function AgentResearchAuditRoute() {
  return <AgentResearchAuditScreen route={adminAgentResearchAuditRoute()} />;
}
