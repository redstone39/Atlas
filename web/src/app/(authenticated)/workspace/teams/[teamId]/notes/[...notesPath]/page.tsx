import { notFound } from "next/navigation";

import { NotesRoutePage } from "../../../../../notes-route";

export default async function WorkspaceTeamNotesDetailRoute({ params }: { params: Promise<{ teamId?: string; notesPath?: string[] }> }) {
  const { teamId, notesPath } = await params;
  if (!teamId || !notesPath) notFound();
  return <NotesRoutePage scopeType="team" scopeId={teamId} notesPath={notesPath} workspace />;
}
