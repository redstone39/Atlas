import type { AppRoute } from "@/shared/routes";
import { AuditScreen } from "../screen";

export function AgentResearchAuditScreen({ route }: { route: AppRoute }) {
  return <AuditScreen route={route} />;
}
