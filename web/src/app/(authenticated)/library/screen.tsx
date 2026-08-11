"use client";

import { KnowledgeLibraryPage } from "@/components/pages/KnowledgeLibraryPage";
import { WorkspaceScreen } from "../workspace/screen";

export function LibraryScreen() {
  return (
    <WorkspaceScreen
      activeView="/library"
      libraryContent={<KnowledgeLibraryPage />}
    />
  );
}
