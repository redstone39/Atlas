import { DocumentLibraryFeature } from "../../features/document-library/index";
import type { SessionState } from "../../features/identity-session/index";
import { teamAdministrationApi } from "../../features/team-administration/index";
import { workspaceApi } from "../../features/workspace/index";
import type { DocumentTagRef } from "../../shared/document-contracts";

export function AdminDocumentLibraryPage({
  session,
  initialScope,
  onNotice,
  onRefresh,
}: {
  session: SessionState;
  initialScope: DocumentTagRef | null;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  return (
    <DocumentLibraryFeature
      session={session}
      initialScope={initialScope}
      loadTeams={teamAdministrationApi.listTeams}
      loadWorkspaceScope={workspaceApi.workspaceTagScope}
      onNotice={onNotice}
      onRefresh={onRefresh}
    />
  );
}
