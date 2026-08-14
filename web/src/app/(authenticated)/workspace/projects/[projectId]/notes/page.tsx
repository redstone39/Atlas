import { notFound } from "next/navigation";

import { NotesRoutePage } from "../../../../notes-route";

export default async function WorkspaceProjectNotesRoute({ params }: { params: Promise<{ projectId?: string }> }) {
  const { projectId } = await params;
  if (!projectId) notFound();
  return <NotesRoutePage scopeType="project" scopeId={projectId} workspace />;
}
