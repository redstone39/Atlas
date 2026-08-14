import { notFound } from "next/navigation";

import type { NoteScopeType, NotesSurface } from "@/features/notes";
import { NotesScreen } from "./notes-screen";

export function NotesRoutePage({ scopeType, scopeId, notesPath = [], workspace = false }: { scopeType: NoteScopeType; scopeId: string; notesPath?: string[]; workspace?: boolean }) {
  let surface: NotesSurface;
  if (notesPath.length === 0) surface = { view: "list" };
  else if (notesPath.length === 1 && notesPath[0] === "trash") surface = { view: "trash" };
  else if (notesPath.length === 1) surface = { view: "editor", noteId: notesPath[0] };
  else if (notesPath.length === 2 && notesPath[1] === "history") surface = { view: "history", noteId: notesPath[0] };
  else if (notesPath.length === 3 && notesPath[1] === "history") surface = { view: "preview", noteId: notesPath[0], savepointId: notesPath[2] };
  else notFound();

  return <NotesScreen scopeType={scopeType} scopeId={scopeId} surface={surface} workspace={workspace} />;
}
