import { notFound } from "next/navigation";

import { adminAgentResearchAuditRoute } from "@/shared/routes";
import { AgentResearchAuditScreen } from "../screen";

export default async function AgentResearchAuditDetailRoute({
  params,
}: {
  params: Promise<{ researchId?: string }>;
}) {
  const { researchId } = await params;
  if (!researchId) notFound();
  return (
    <AgentResearchAuditScreen
      route={adminAgentResearchAuditRoute(researchId)}
    />
  );
}
