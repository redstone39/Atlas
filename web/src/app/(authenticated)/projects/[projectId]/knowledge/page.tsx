import { notFound } from "next/navigation";

import { ProjectsScreen } from "../../screen";

export default async function ProjectKnowledgeRoute({
  params,
}: {
  params: Promise<{ projectId?: string }>;
}) {
  const { projectId } = await params;
  if (!projectId) notFound();
  return <ProjectsScreen projectId={projectId} />;
}
