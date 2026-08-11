import { notFound } from "next/navigation";

import { adminAuditConversationRoute } from "@/shared/routes";
import { AuditTranscriptScreen } from "./screen";

export default async function AuditTranscriptRoute({
  params,
}: {
  params: Promise<{ conversationId?: string }>;
}) {
  const { conversationId } = await params;
  if (!conversationId) notFound();
  return (
    <AuditTranscriptScreen
      route={adminAuditConversationRoute(conversationId, "transcript")}
    />
  );
}
