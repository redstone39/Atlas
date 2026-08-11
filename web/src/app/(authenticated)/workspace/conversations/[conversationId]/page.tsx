import { notFound } from "next/navigation";

import { workspaceConversationRoute } from "@/shared/routes";
import { ConversationScreen } from "./screen";

export default async function ConversationRoute({
  params,
}: {
  params: Promise<{ conversationId?: string }>;
}) {
  const { conversationId } = await params;
  if (!conversationId) notFound();
  return (
    <ConversationScreen route={workspaceConversationRoute(conversationId)} />
  );
}
