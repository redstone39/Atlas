import { notFound } from "next/navigation";
import { NotesRoutePage } from "../../../notes-route";

export default async function TeamNotesRoute({ params }: { params: Promise<{ teamId?: string }> }) {
  const { teamId } = await params;
  if (!teamId) notFound();
  return <NotesRoutePage scopeType="team" scopeId={teamId} />;
}
