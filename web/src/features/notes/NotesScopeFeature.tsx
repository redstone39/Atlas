import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { AdminBreadcrumb, AdminResourceUnavailable } from "../../shared/admin-detail";
import { LoadErrorState, LoadingState } from "../../shared/product-ui";
import { scopeNotesRoute, type AppRoute } from "../../shared/routes";
import { ScopeSecondaryNavigation } from "../../shared/scope-secondary-navigation";
import { notesApi } from "./api";
import { NoteEditorView } from "./NoteEditorView";
import { NoteHistoryView } from "./NoteHistoryView";
import { NotesListView } from "./NotesListView";
import { NoteSavepointView } from "./NoteSavepointView";
import type { NoteScope, NotesScopeFeatureProps } from "./types";

export function NotesScopeFeature({ scopeType, scopeId, surface, workspace = false, onNavigate }: NotesScopeFeatureProps) {
  const { t } = useTranslation();
  const [scopes, setScopes] = useState<NoteScope[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const generationRef = useRef(0);

  useEffect(() => {
    const generation = ++generationRef.current;
    setLoading(true);
    setLoadError(false);
    notesApi.listScopes().then((result) => {
      if (generation !== generationRef.current) return;
      setScopes(result.items);
      setLoading(false);
    }).catch(() => {
      if (generation !== generationRef.current) return;
      setLoading(false);
      setLoadError(true);
    });
    return () => {
      if (generation === generationRef.current) generationRef.current += 1;
    };
  }, [reloadKey]);

  const scope = useMemo(
    () => scopes.find((candidate) => candidate.scope_type === scopeType && candidate.scope_id === scopeId) ?? null,
    [scopeId, scopeType, scopes],
  );
  const directoryRoute = `${workspace ? "/workspace" : ""}/${scopeType === "project" ? "projects" : "teams"}` as AppRoute;

  if (loading) return <LoadingState title={t("notes.loadingScope")} />;
  if (loadError) return <LoadErrorState title={t("notes.scopeLoadFailed")} description={t("notes.scopeLoadFailedDescription")} retryLabel={t("admin.retry")} onRetry={() => setReloadKey((current) => current + 1)} />;
  if (!scope) return <AdminResourceUnavailable onBack={() => onNavigate(directoryRoute)} />;

  const breadcrumbItems: Array<{ label: string; route?: AppRoute }> = [
    { label: t(scopeType === "project" ? "knowledgeScope.projectsTitle" : "knowledgeScope.teamsTitle"), route: directoryRoute },
    { label: scope.label },
    { label: t("notes.notesTab"), route: scopeNotesRoute(scopeType, scopeId, { kind: "list" }, workspace) },
  ];
  if (surface.view === "trash") breadcrumbItems.push({ label: t("notes.trash") });
  if (surface.view === "editor") breadcrumbItems.push({ label: t("notes.editorBreadcrumb") });
  if (surface.view === "history" || surface.view === "preview") breadcrumbItems.push({ label: t("notes.history") });
  if (surface.view === "preview") breadcrumbItems.push({ label: t("notes.previewBreadcrumb") });

  return (
    <section className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <AdminBreadcrumb items={breadcrumbItems} onNavigate={onNavigate} />
      <ScopeSecondaryNavigation scopeType={scopeType} scopeId={scopeId} active="notes" workspace={workspace} onNavigate={onNavigate} />
      {surface.view === "list" && <NotesListView key={`${scopeType}:${scopeId}:active`} scope={scope} lifecycle="active" workspace={workspace} onNavigate={onNavigate} />}
      {surface.view === "trash" && <NotesListView key={`${scopeType}:${scopeId}:trashed`} scope={scope} lifecycle="trashed" workspace={workspace} onNavigate={onNavigate} />}
      {surface.view === "editor" && <NoteEditorView key={`${scopeType}:${scopeId}:${surface.noteId}`} scope={scope} noteId={surface.noteId} workspace={workspace} onNavigate={onNavigate} />}
      {surface.view === "history" && <NoteHistoryView key={`${scopeType}:${scopeId}:${surface.noteId}:history`} scope={scope} noteId={surface.noteId} workspace={workspace} onNavigate={onNavigate} />}
      {surface.view === "preview" && <NoteSavepointView key={`${scopeType}:${scopeId}:${surface.noteId}:${surface.savepointId}`} scope={scope} noteId={surface.noteId} savepointId={surface.savepointId} workspace={workspace} onNavigate={onNavigate} />}
    </section>
  );
}
