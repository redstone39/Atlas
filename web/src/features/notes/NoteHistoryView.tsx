import { ChevronDown, ChevronRight, History } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "../../components/ui/empty";
import { LoadErrorState, LoadingState, PageHeader } from "../../shared/product-ui";
import { scopeNotesRoute } from "../../shared/routes";
import { notesApi } from "./api";
import { NoteChangeSetView } from "./NoteChangeSetView";
import type { NoteDetail, NoteRevision, NoteSavepointSummary, NoteScope, NotesScopeFeatureProps } from "./types";

export function NoteHistoryView({ scope, noteId, workspace, onNavigate }: Pick<NotesScopeFeatureProps, "workspace" | "onNavigate"> & { scope: NoteScope; noteId: string }) {
  const { t, i18n } = useTranslation();
  const [note, setNote] = useState<NoteDetail | null>(null);
  const [revisions, setRevisions] = useState<NoteRevision[]>([]);
  const [savepoints, setSavepoints] = useState<NoteSavepointSummary[]>([]);
  const [activityOpen, setActivityOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const generationRef = useRef(0);

  useEffect(() => {
    const generation = ++generationRef.current;
    setLoading(true);
    setLoadError(false);
    Promise.all([
      notesApi.getNote(noteId),
      notesApi.listRevisions(noteId),
      notesApi.listSavepoints(noteId),
    ]).then(([loadedNote, revisionResult, savepointResult]) => {
      if (generation !== generationRef.current) return;
      if (loadedNote.scope.scope_type !== scope.scope_type || loadedNote.scope.scope_id !== scope.scope_id) throw new Error("Scope mismatch");
      setNote(loadedNote);
      setRevisions(revisionResult.items);
      setSavepoints(savepointResult.items);
      setLoading(false);
    }).catch(() => {
      if (generation !== generationRef.current) return;
      setLoading(false);
      setLoadError(true);
    });
    return () => {
      if (generation === generationRef.current) generationRef.current += 1;
    };
  }, [noteId, reloadKey, scope.scope_id, scope.scope_type]);

  if (loading) return <LoadingState title={t("notes.loadingHistory")} />;
  if (loadError || !note) return <LoadErrorState title={t("notes.historyUnavailable")} description={t("notes.historyUnavailableDescription")} retryLabel={t("admin.retry")} onRetry={() => setReloadKey((current) => current + 1)} />;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title={t("notes.historyTitle", { title: note.title })} description={t("notes.historyDescription")} />
      <div className="flex justify-end"><Button variant="outline" onClick={() => onNavigate(scopeNotesRoute(scope.scope_type, scope.scope_id, { kind: "editor", noteId }, workspace))}>{t("notes.backToEditor")}</Button></div>

      <Card>
        <CardHeader><CardTitle>{t("notes.savepoints")}</CardTitle><CardDescription>{t("notes.savepointsDescription")}</CardDescription></CardHeader>
        <CardContent>
          {savepoints.length === 0 ? <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><History /></EmptyMedia><EmptyTitle>{t("notes.noSavepoints")}</EmptyTitle><EmptyDescription>{t("notes.noSavepointsDescription")}</EmptyDescription></EmptyHeader></Empty> : (
            <div className="flex flex-col gap-2">{savepoints.map((savepoint) => (
              <button key={savepoint.savepoint_id} type="button" className="flex w-full items-center justify-between gap-3 rounded-md border p-3 text-left" onClick={() => onNavigate(scopeNotesRoute(scope.scope_type, scope.scope_id, { kind: "preview", noteId, savepointId: savepoint.savepoint_id }, workspace))}>
                <span><span className="block font-medium">{t("notes.savepointNumber", { number: savepoint.sequence })}</span><span className="text-sm text-muted-foreground">{new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(savepoint.created_at))} · {t("notes.coveredRevision", { number: savepoint.covered_revision })}</span></span><ChevronRight />
              </button>
            ))}</div>
          )}
        </CardContent>
      </Card>

      <Collapsible open={activityOpen} onOpenChange={setActivityOpen}>
        <Card>
          <CardHeader>
            <CardTitle><h2>{t("notes.activityLog")}</h2></CardTitle>
            <CardDescription>{t("notes.activityLogDescription", { count: revisions.length })}</CardDescription>
            <CardAction>
              <CollapsibleTrigger asChild>
                <Button variant="outline" size="sm" className="group">
                  {t(activityOpen ? "notes.hideActivityLog" : "notes.showActivityLog")}
                  <ChevronDown data-icon="inline-end" className="transition-transform group-data-[state=open]:rotate-180" />
                </Button>
              </CollapsibleTrigger>
            </CardAction>
          </CardHeader>
          <CollapsibleContent>
            <CardContent className="flex flex-col gap-3">
              {revisions.length === 0 ? (
                <Empty className="border">
                  <EmptyHeader>
                    <EmptyMedia variant="icon"><History /></EmptyMedia>
                    <EmptyTitle>{t("notes.noRevisions")}</EmptyTitle>
                    <EmptyDescription>{t("notes.noRevisionsDescription")}</EmptyDescription>
                  </EmptyHeader>
                </Empty>
              ) : revisions.map((revision) => (
                <Card key={revision.revision_id}>
                  <CardHeader><div className="flex flex-wrap items-start justify-between gap-2"><div><CardTitle>{t("notes.revisionNumber", { number: revision.sequence })}</CardTitle><CardDescription>{revision.actor_id} · {new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(revision.server_timestamp))}</CardDescription></div><Badge variant="outline">{t(`notes.event.${revision.event_kind}`)}</Badge></div></CardHeader>
                  <CardContent><NoteChangeSetView changeSet={revision.change_set} /></CardContent>
                </Card>
              ))}
            </CardContent>
          </CollapsibleContent>
        </Card>
      </Collapsible>
    </div>
  );
}
