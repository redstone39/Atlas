import { notFound } from "next/navigation";

import { adminProjectDetailRoute } from "@/shared/routes";
import { ProjectAccessScreen } from "./screen";

export default async function ProjectAccessRoute({
  params,
}: {
  params: Promise<{ projectId?: string }>;
}) {
  const { projectId } = await params;
  if (!projectId) notFound();
  return (
    <ProjectAccessScreen route={adminProjectDetailRoute(projectId, "access")} />
  );
}
