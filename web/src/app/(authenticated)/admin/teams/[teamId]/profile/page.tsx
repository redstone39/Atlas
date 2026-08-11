import { notFound } from "next/navigation";

import { adminTeamDetailRoute } from "@/shared/routes";
import { TeamProfileScreen } from "./screen";

export default async function TeamProfileRoute({
  params,
}: {
  params: Promise<{ teamId?: string }>;
}) {
  const { teamId } = await params;
  if (!teamId) notFound();
  return <TeamProfileScreen route={adminTeamDetailRoute(teamId, "profile")} />;
}
