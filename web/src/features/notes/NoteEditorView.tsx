import { ArchiveRestore, History, Pencil, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Field, FieldGroup, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Spinner } from "../../components/ui/spinner";
import { LoadErrorState, LoadingState, PageHeader } from "../../shared/product-ui";
import { scopeNotesRoute } from "../../shared/routes";
import { CollaborativeNoteEditor } from "./CollaborativeNoteEditor";
import { notesApi } from "./api";
import type { NoteCategory, NoteDetail, NoteScope, NotesScopeFeatureProps } from "./types";

const NO_CATEGORY = "__none__";

export function NoteEditorView({
  scope,
  noteId,
  workspace,
  onNavigate,
}: Pick<NotesScopeFeatureProps, "workspace" | "onNavigate"> & {
  scope: NoteScope;
  noteId: string;
}) {
  const { t } = useTranslation();
  const [note, setNote] = useState<NoteDetail | null>(null);
  const [categories, setCategories] = useState<NoteCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [editOpen, setEditOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [categoryId, setCategoryId] = useState(NO_CATEGORY);
  const [saving, setSaving] = useState(false);
  const generationRef = useRef(0);
  const mutationGeneration = useRef(0);
  const mutationInFlight = useRef(false);
  useEffect(() => () => {
    mutationGeneration.current += 1;
  }, [noteId, scope.scope_id, scope.scope_type]);

  useEffect(() => {
    const generation = ++generationRef.current;
    setLoading(true);
    setLoadError(false);
    Promise.all([
      notesApi.getNote(noteId),
      notesApi.listCategories(scope.scope_type, scope.scope_id, "active"),
    ]).then(([loadedNote, categoryResult]) => {
      if (generation !== generationRef.current) return;
      if (loadedNote.scope.scope_type !== scope.scope_type || loadedNote.scope.scope_id !== scope.scope_id) {
        throw new Error("Scope mismatch");
      }
      setNote(loadedNote);
      setCategories(categoryResult.items);
      setTitle(loadedNote.title);
      setCategoryId(loadedNote.category_id ?? NO_CATEGORY);
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

  async function saveMetadata() {
    if (!note || !title.trim()) return;
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    const generation = ++mutationGeneration.current;
    setSaving(true);
    try {
      const updated = await notesApi.updateNote(note.note_id, note.metadata_revision, {
        title: title.trim(),
        categoryId: categoryId === NO_CATEGORY ? null : categoryId,
      });
      if (generation !== mutationGeneration.current) return;
      setNote(updated);
      setEditOpen(false);
      toast.success(t("notes.metadataSaved"));
    } catch {
      if (generation !== mutationGeneration.current) return;
      toast.error(t("notes.metadataSaveFailed"));
    } finally {
      mutationInFlight.current = false;
      if (generation === mutationGeneration.current) setSaving(false);
    }
  }

  async function changeLifecycle() {
    if (!note) return;
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    const generation = ++mutationGeneration.current;
    setSaving(true);
    try {
      const updated = note.lifecycle_status === "active"
        ? await notesApi.trashNote(note)
        : await notesApi.restoreNote(note);
      if (generation !== mutationGeneration.current) return;
      setNote(updated);
      toast.success(t(note.lifecycle_status === "active" ? "notes.trashed" : "notes.restored"));
    } catch {
      if (generation !== mutationGeneration.current) return;
      toast.error(t("common.requestFailed"));
    } finally {
      mutationInFlight.current = false;
      if (generation === mutationGeneration.current) setSaving(false);
    }
  }

  if (loading) return <LoadingState title={t("notes.loadingNote")} />;
  if (loadError || !note) {
    return <LoadErrorState title={t("notes.noteUnavailable")} description={t("notes.noteUnavailableDescription")} retryLabel={t("admin.retry")} onRetry={() => setReloadKey((current) => current + 1)} />;
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title={note.title} description={t(note.lifecycle_status === "active" ? "notes.editorDescription" : "notes.trashedEditorDescription")} />
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => onNavigate(scopeNotesRoute(scope.scope_type, scope.scope_id, { kind: "history", noteId }, workspace))}>
          <History data-icon="inline-start" />{t("notes.history")}
        </Button>
        {note.lifecycle_status === "active" && (
          <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
            <Pencil data-icon="inline-start" />{t("notes.editMetadata")}
          </Button>
        )}
        <Button variant="outline" size="sm" disabled={saving} onClick={() => void changeLifecycle()}>
          {note.lifecycle_status === "active" ? <Trash2 data-icon="inline-start" /> : <ArchiveRestore data-icon="inline-start" />}
          {t(note.lifecycle_status === "active" ? "notes.moveToTrash" : "notes.restoreNote")}
        </Button>
      </div>
      <CollaborativeNoteEditor key={`${note.note_id}:${note.collaboration_epoch}`} note={note} />

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("notes.editMetadata")}</DialogTitle><DialogDescription>{t("notes.editMetadataDescription")}</DialogDescription></DialogHeader>
          <FieldGroup>
            <Field><FieldLabel htmlFor="edit-note-title">{t("notes.title")}</FieldLabel><Input id="edit-note-title" value={title} onChange={(event) => setTitle(event.target.value)} /></Field>
            <Field><FieldLabel htmlFor="edit-note-category">{t("notes.category")}</FieldLabel><Select value={categoryId} onValueChange={setCategoryId}><SelectTrigger id="edit-note-category"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value={NO_CATEGORY}>{t("notes.noCategory")}</SelectItem>{categories.map((category) => <SelectItem key={category.category_id} value={category.category_id}>{category.name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
          </FieldGroup>
          <DialogFooter><Button disabled={!title.trim() || saving} onClick={() => void saveMetadata()}>{saving && <Spinner data-icon="inline-start" />}{t("notes.save")}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
