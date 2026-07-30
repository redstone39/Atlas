import { DocumentLibraryFeature } from "../features/document-library/index";
import type { SessionState } from "../features/identity-session/index";
import { teamAdministrationApi } from "../features/team-administration/index";
import { workspaceApi } from "../features/workspace/index";

export function AdminDocumentLibraryPage({
  session,
  onNotice,
  onRefresh,
}: {
  session: SessionState;
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  return (
    <DocumentLibraryFeature
      session={session}
      loadTeams={teamAdministrationApi.listTeams}
      loadWorkspaceScope={workspaceApi.workspaceTagScope}
      onNotice={onNotice}
      onRefresh={onRefresh}
    />
  );
}
