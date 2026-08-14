"use client";

import { EditorContent, useEditor } from "@tiptap/react";
import { History } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "../../components/ui/alert-dialog";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Spinner } from "../../components/ui/spinner";
import { LoadErrorState, LoadingState, PageHeader } from "../../shared/product-ui";
import { scopeNotesRoute } from "../../shared/routes";
import { notesApi } from "./api";
import { noteExtensions } from "./note-extensions";
import { NoteChangeSetView } from "./NoteChangeSetView";
import type { NoteDetail, NoteSavepointPreview, NoteScope, NotesScopeFeatureProps } from "./types";

export function NoteSavepointView({ scope, noteId, savepointId, workspace, onNavigate }: Pick<NotesScopeFeatureProps, "workspace" | "onNavigate"> & { scope: NoteScope; noteId: string; savepointId: string }) {
  const { t, i18n } = useTranslation();
  const [note, setNote] = useState<NoteDetail | null>(null);
  const [savepoint, setSavepoint] = useState<NoteSavepointPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const generationRef = useRef(0);
  const mutationGeneration = useRef(0);
  const mutationInFlight = useRef(false);
  useEffect(() => () => {
    mutationGeneration.current += 1;
  }, [noteId, savepointId, scope.scope_id, scope.scope_type]);

  useEffect(() => {
    const generation = ++generationRef.current;
    setLoading(true);
    setLoadError(false);
    Promise.all([notesApi.getNote(noteId), notesApi.getSavepoint(noteId, savepointId)]).then(([loadedNote, loadedSavepoint]) => {
      if (generation !== generationRef.current) return;
      if (loadedNote.scope.scope_type !== scope.scope_type || loadedNote.scope.scope_id !== scope.scope_id) throw new Error("Scope mismatch");
      setNote(loadedNote);
      setSavepoint(loadedSavepoint);
      setLoading(false);
    }).catch(() => {
      if (generation !== generationRef.current) return;
      setLoading(false);
      setLoadError(true);
    });
    return () => {
      if (generation === generationRef.current) generationRef.current += 1;
    };
  }, [noteId, reloadKey, savepointId, scope.scope_id, scope.scope_type]);

  async function restoreBody() {
    if (!note) return;
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    const generation = ++mutationGeneration.current;
    setRestoring(true);
    try {
      const result = await notesApi.restoreBody(note, savepointId);
      if (generation !== mutationGeneration.current) return;
      toast.success(t("notes.restoreBodySucceeded", { revision: result.revision.sequence, savepoint: result.savepoint.sequence }));
      onNavigate(scopeNotesRoute(scope.scope_type, scope.scope_id, { kind: "editor", noteId }, workspace));
    } catch {
      if (generation !== mutationGeneration.current) return;
      toast.error(t("notes.restoreBodyFailed"));
    } finally {
      mutationInFlight.current = false;
      if (generation === mutationGeneration.current) setRestoring(false);
    }
  }

  if (loading) return <LoadingState title={t("notes.loadingPreview")} />;
  if (loadError || !note || !savepoint) return <LoadErrorState title={t("notes.previewUnavailable")} description={t("notes.previewUnavailableDescription")} retryLabel={t("admin.retry")} onRetry={() => setReloadKey((current) => current + 1)} />;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title={t("notes.previewTitle", { number: savepoint.sequence })} description={t("notes.previewDescription", { title: note.title })} />
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" onClick={() => onNavigate(scopeNotesRoute(scope.scope_type, scope.scope_id, { kind: "history", noteId }, workspace))}><History data-icon="inline-start" />{t("notes.backToHistory")}</Button>
        {note.lifecycle_status === "active" && (
          <AlertDialog>
            <AlertDialogTrigger asChild><Button disabled={restoring}>{restoring && <Spinner data-icon="inline-start" />}{t("notes.restoreBody")}</Button></AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader><AlertDialogTitle>{t("notes.restoreBodyConfirmTitle")}</AlertDialogTitle><AlertDialogDescription>{t("notes.restoreBodyConfirmDescription")}</AlertDialogDescription></AlertDialogHeader>
              <AlertDialogFooter><AlertDialogCancel>{t("notes.cancel")}</AlertDialogCancel><AlertDialogAction onClick={() => void restoreBody()}>{t("notes.restoreBodyConfirm")}</AlertDialogAction></AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
      <Card><CardHeader><CardTitle>{t("notes.historicalBody")}</CardTitle><CardDescription>{new Intl.DateTimeFormat(i18n.language, { dateStyle: "long", timeStyle: "short" }).format(new Date(savepoint.created_at))}</CardDescription></CardHeader><CardContent><ReadOnlyBody noteId={noteId} content={savepoint.canonical_body} /></CardContent></Card>
      <Card><CardHeader><CardTitle>{t("notes.changesFromPrevious")}</CardTitle><CardDescription>{t("notes.changesFromPreviousDescription")}</CardDescription></CardHeader><CardContent><NoteChangeSetView changeSet={savepoint.aggregate_change_set} /></CardContent></Card>
    </div>
  );
}

function ReadOnlyBody({ noteId, content }: { noteId: string; content: NoteSavepointPreview["canonical_body"] }) {
  const { t } = useTranslation();
  const editor = useEditor({
    immediatelyRender: false,
    editable: false,
    editorProps: {
      attributes: { "aria-label": t("notes.historicalBody") },
    },
    content,
    extensions: noteExtensions({ noteId, live: false }),
  }, [content, noteId, t]);
  return <EditorContent editor={editor} className="notes-editor min-h-48 rounded-md border bg-muted/30 p-4" />;
}
