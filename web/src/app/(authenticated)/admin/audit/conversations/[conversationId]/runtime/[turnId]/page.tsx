import { notFound } from "next/navigation";

import { adminAuditConversationRoute } from "@/shared/routes";
import { AuditRuntimeScreen } from "./screen";

export default async function AuditRuntimeRoute({
  params,
}: {
  params: Promise<{ conversationId?: string; turnId?: string }>;
}) {
  const { conversationId, turnId } = await params;
  if (!conversationId || !turnId) notFound();
  return (
    <AuditRuntimeScreen
      route={adminAuditConversationRoute(conversationId, "runtime", turnId)}
    />
  );
}
