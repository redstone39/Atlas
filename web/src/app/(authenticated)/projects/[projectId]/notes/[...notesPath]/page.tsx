import { notFound } from "next/navigation";
import { NotesRoutePage } from "../../../../notes-route";

export default async function ProjectNotesDetailRoute({ params }: { params: Promise<{ projectId?: string; notesPath?: string[] }> }) {
  const { projectId, notesPath } = await params;
  if (!projectId || !notesPath) notFound();
  return <NotesRoutePage scopeType="project" scopeId={projectId} notesPath={notesPath} />;
}
