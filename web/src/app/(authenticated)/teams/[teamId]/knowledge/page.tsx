import { notFound } from "next/navigation";

import { TeamsScreen } from "../../screen";

export default async function TeamKnowledgeRoute({
  params,
}: {
  params: Promise<{ teamId?: string }>;
}) {
  const { teamId } = await params;
  if (!teamId) notFound();
  return <TeamsScreen teamId={teamId} />;
}
