"use client";

import { NotesScopePage } from "@/components/pages/NotesScopePage";
import type { NoteScopeType, NotesSurface } from "@/features/notes";
import { useAuthenticatedShell } from "./layout";

export function NotesScreen({ scopeType, scopeId, surface, workspace = false }: { scopeType: NoteScopeType; scopeId: string; surface: NotesSurface; workspace?: boolean }) {
  const { navigate } = useAuthenticatedShell();
  return <NotesScopePage scopeType={scopeType} scopeId={scopeId} surface={surface} workspace={workspace} onNavigate={navigate} />;
}
