import { notFound } from "next/navigation";

import { adminTeamDetailRoute } from "@/shared/routes";
import { TeamMembersScreen } from "./screen";

export default async function TeamMembersRoute({
  params,
}: {
  params: Promise<{ teamId?: string }>;
}) {
  const { teamId } = await params;
  if (!teamId) notFound();
  return <TeamMembersScreen route={adminTeamDetailRoute(teamId, "members")} />;
}
