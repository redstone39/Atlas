import { notFound } from "next/navigation";

import { adminProjectDetailRoute } from "@/shared/routes";
import { ProjectProfileScreen } from "./screen";

export default async function ProjectProfileRoute({
  params,
}: {
  params: Promise<{ projectId?: string }>;
}) {
  const { projectId } = await params;
  if (!projectId) notFound();
  return (
    <ProjectProfileScreen route={adminProjectDetailRoute(projectId, "profile")} />
  );
}
