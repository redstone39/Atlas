import { ArchiveRestore, ChevronRight, FolderPlus, NotebookPen, Plus, Settings2, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "../../components/ui/dialog";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "../../components/ui/empty";
import { Field, FieldGroup, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Spinner } from "../../components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { useIsMobile } from "../../hooks/use-mobile";
import { activateOnEnterOrSpace, clickableSurfaceClassName, LoadErrorState, LoadingState, PageHeader } from "../../shared/product-ui";
import { scopeNotesRoute } from "../../shared/routes";
import { notesApi } from "./api";
import type { NoteCategory, NoteLifecycleStatus, NoteScope, NoteSummary, NotesScopeFeatureProps } from "./types";

const ALL_CATEGORIES = "__all__";
const NO_CATEGORY = "__none__";

export function NotesListView({
  scope,
  lifecycle,
  workspace,
  onNavigate,
}: Pick<NotesScopeFeatureProps, "workspace" | "onNavigate"> & {
  scope: NoteScope;
  lifecycle: NoteLifecycleStatus;
}) {
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  const [notes, setNotes] = useState<NoteSummary[]>([]);
  const [categories, setCategories] = useState<NoteCategory[]>([]);
  const [trashedCategories, setTrashedCategories] = useState<NoteCategory[]>([]);
  const [categoryFilter, setCategoryFilter] = useState(ALL_CATEGORIES);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [newNoteCategory, setNewNoteCategory] = useState(NO_CATEGORY);
  const [saving, setSaving] = useState(false);
  const requestGeneration = useRef(0);
  const mutationGeneration = useRef(0);
  const mutationInFlight = useRef(false);
  useEffect(() => () => {
    mutationGeneration.current += 1;
  }, [lifecycle, scope.scope_id, scope.scope_type]);

  useEffect(() => {
    const generation = ++requestGeneration.current;
    setLoading(true);
    setLoadError(false);
    const categoryId = categoryFilter !== ALL_CATEGORIES && categoryFilter !== NO_CATEGORY
      ? categoryFilter
      : undefined;
    Promise.all([
      notesApi.listNotes(scope.scope_type, scope.scope_id, lifecycle, categoryId),
      notesApi.listCategories(scope.scope_type, scope.scope_id, "active"),
      notesApi.listCategories(scope.scope_type, scope.scope_id, "trashed"),
    ]).then(([noteResult, activeResult, trashedResult]) => {
      if (generation !== requestGeneration.current) return;
      const visibleNotes = categoryFilter === NO_CATEGORY
        ? noteResult.items.filter((note) => note.category_id === null)
        : noteResult.items;
      setNotes(visibleNotes);
      setCategories(activeResult.items);
      setTrashedCategories(trashedResult.items);
      setLoading(false);
    }).catch(() => {
      if (generation !== requestGeneration.current) return;
      setLoading(false);
      setLoadError(true);
    });
    return () => {
      if (generation === requestGeneration.current) requestGeneration.current += 1;
    };
  }, [categoryFilter, lifecycle, reloadKey, scope.scope_id, scope.scope_type]);

  const listRoute = scopeNotesRoute(scope.scope_type, scope.scope_id, { kind: "list" }, workspace);
  const trashRoute = scopeNotesRoute(scope.scope_type, scope.scope_id, { kind: "trash" }, workspace);

  async function createNote() {
    if (!title.trim()) return;
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    const generation = ++mutationGeneration.current;
    setSaving(true);
    try {
      const note = await notesApi.createNote({
        scopeType: scope.scope_type,
        scopeId: scope.scope_id,
        title: title.trim(),
        categoryId: newNoteCategory === NO_CATEGORY ? null : newNoteCategory,
      });
      if (generation !== mutationGeneration.current) return;
      setCreateOpen(false);
      setTitle("");
      setNewNoteCategory(NO_CATEGORY);
      onNavigate(scopeNotesRoute(scope.scope_type, scope.scope_id, { kind: "editor", noteId: note.note_id }, workspace));
    } catch {
      if (generation !== mutationGeneration.current) return;
      toast.error(t("notes.createFailed"));
    } finally {
      mutationInFlight.current = false;
      if (generation === mutationGeneration.current) setSaving(false);
    }
  }

  async function changeLifecycle(note: NoteSummary) {
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    setSaving(true);
    const generation = ++mutationGeneration.current;
    try {
      if (note.lifecycle_status === "active") await notesApi.trashNote(note);
      else await notesApi.restoreNote(note);
      if (generation !== mutationGeneration.current) return;
      toast.success(t(note.lifecycle_status === "active" ? "notes.trashed" : "notes.restored"));
      setReloadKey((current) => current + 1);
    } catch {
      if (generation !== mutationGeneration.current) return;
      toast.error(t("common.requestFailed"));
    }
    finally {
      mutationInFlight.current = false;
      if (generation === mutationGeneration.current) setSaving(false);
    }
  }

  function openNote(note: NoteSummary) {
    onNavigate(scopeNotesRoute(scope.scope_type, scope.scope_id, { kind: "editor", noteId: note.note_id }, workspace));
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t("notes.listTitle", { scope: scope.label })}
        description={lifecycle === "trashed" ? t("notes.trashDescription") : undefined}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Button variant={lifecycle === "active" ? "secondary" : "ghost"} size="sm" onClick={() => onNavigate(listRoute)}>
            <NotebookPen data-icon="inline-start" />{t("notes.activeNotes")}
          </Button>
          <Button variant={lifecycle === "trashed" ? "secondary" : "ghost"} size="sm" onClick={() => onNavigate(trashRoute)}>
            <Trash2 data-icon="inline-start" />{t("notes.trash")}
          </Button>
        </div>
        <div className="flex flex-wrap gap-2">
          <Dialog open={categoryOpen} onOpenChange={setCategoryOpen}>
            <DialogTrigger asChild><Button variant="outline" size="sm"><Settings2 data-icon="inline-start" />{t("notes.manageCategories")}</Button></DialogTrigger>
            <DialogContent>
              <DialogHeader><DialogTitle>{t("notes.manageCategories")}</DialogTitle><DialogDescription>{t("notes.categoriesDescription")}</DialogDescription></DialogHeader>
              <CategoryManager
                scope={scope}
                active={categories}
                trashed={trashedCategories}
                onChanged={() => setReloadKey((current) => current + 1)}
              />
            </DialogContent>
          </Dialog>
          {lifecycle === "active" && (
            <Dialog open={createOpen} onOpenChange={setCreateOpen}>
              <DialogTrigger asChild><Button size="sm"><Plus data-icon="inline-start" />{t("notes.newNote")}</Button></DialogTrigger>
              <DialogContent>
                <DialogHeader><DialogTitle>{t("notes.newNote")}</DialogTitle><DialogDescription>{t("notes.newNoteDescription")}</DialogDescription></DialogHeader>
                <FieldGroup>
                  <Field><FieldLabel htmlFor="note-title">{t("notes.title")}</FieldLabel><Input id="note-title" value={title} onChange={(event) => setTitle(event.target.value)} /></Field>
                  <Field><FieldLabel>{t("notes.category")}</FieldLabel><CategorySelect value={newNoteCategory} categories={categories} onChange={setNewNoteCategory} ariaLabel={t("notes.category")} /></Field>
                </FieldGroup>
                <DialogFooter><Button disabled={!title.trim() || saving} onClick={createNote}>{saving && <Spinner data-icon="inline-start" />}{t("notes.create")}</Button></DialogFooter>
              </DialogContent>
            </Dialog>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-4">
          <div className="max-w-xs">
            <CategorySelect value={categoryFilter} categories={categories} onChange={setCategoryFilter} includeAll ariaLabel={t("notes.categoryFilter")} />
          </div>
          {loading ? <LoadingState title={t("notes.loadingList")} /> : loadError ? (
            <LoadErrorState title={t("notes.loadFailed")} description={t("notes.loadFailedDescription")} retryLabel={t("admin.retry")} onRetry={() => setReloadKey((current) => current + 1)} />
          ) : notes.length === 0 ? (
            <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><NotebookPen /></EmptyMedia><EmptyTitle>{t("notes.emptyTitle")}</EmptyTitle><EmptyDescription>{t("notes.emptyDescription")}</EmptyDescription></EmptyHeader></Empty>
          ) : isMobile ? (
            <div className="grid gap-3">
              {notes.map((note) => (
                <button key={note.note_id} type="button" className="flex min-h-11 w-full items-center justify-between gap-3 rounded-md border bg-card p-3 text-left" onClick={() => openNote(note)}>
                  <span className="min-w-0"><span className="block truncate font-medium">{note.title}</span><span className="text-sm text-muted-foreground">{new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(note.updated_at))}</span></span><ChevronRight />
                </button>
              ))}
            </div>
          ) : (
            <Table><TableHeader><TableRow><TableHead>{t("notes.title")}</TableHead><TableHead>{t("notes.category")}</TableHead><TableHead>{t("notes.updated")}</TableHead><TableHead className="text-right">{t("notes.actions")}</TableHead></TableRow></TableHeader>
              <TableBody>{notes.map((note) => (
                <TableRow key={note.note_id} className={clickableSurfaceClassName} role="button" tabIndex={0} onClick={() => openNote(note)} onKeyDown={(event) => activateOnEnterOrSpace(event, () => openNote(note))}>
                  <TableCell className="font-medium">{note.title}</TableCell><TableCell>{categories.find((category) => category.category_id === note.category_id)?.name ?? t("notes.noCategory")}</TableCell><TableCell>{new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(note.updated_at))}</TableCell>
                  <TableCell className="text-right"><Button variant="outline" size="sm" disabled={saving} onClick={(event) => { event.stopPropagation(); void changeLifecycle(note); }}>{note.lifecycle_status === "active" ? <Trash2 data-icon="inline-start" /> : <ArchiveRestore data-icon="inline-start" />}{t(note.lifecycle_status === "active" ? "notes.moveToTrash" : "notes.restoreNote")}</Button></TableCell>
                </TableRow>
              ))}</TableBody>
            </Table>
          )}
      </div>
    </div>
  );
}

function CategorySelect({ value, categories, onChange, includeAll = false, ariaLabel }: { value: string; categories: NoteCategory[]; onChange: (value: string) => void; includeAll?: boolean; ariaLabel: string }) {
  const { t } = useTranslation();
  return <Select value={value} onValueChange={onChange}><SelectTrigger aria-label={ariaLabel}><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{includeAll && <SelectItem value={ALL_CATEGORIES}>{t("notes.allCategories")}</SelectItem>}<SelectItem value={NO_CATEGORY}>{t("notes.noCategory")}</SelectItem>{categories.map((category) => <SelectItem key={category.category_id} value={category.category_id}>{category.name}</SelectItem>)}</SelectGroup></SelectContent></Select>;
}

function CategoryManager({ scope, active, trashed, onChanged }: { scope: NoteScope; active: NoteCategory[]; trashed: NoteCategory[]; onChanged: () => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<NoteCategory | null>(null);
  const [busy, setBusy] = useState(false);

  const mutationGeneration = useRef(0);
  const mutationInFlight = useRef(false);
  useEffect(() => () => {
    mutationGeneration.current += 1;
  }, [scope.scope_id, scope.scope_type]);
  async function createOrRename() {
    if (!name.trim()) return;
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    const generation = ++mutationGeneration.current;
    setBusy(true);
    try {
      if (editing) await notesApi.updateCategory(editing, name.trim());
      else await notesApi.createCategory(scope.scope_type, scope.scope_id, name.trim());
      if (generation !== mutationGeneration.current) return;
      setName("");
      setEditing(null);
      onChanged();
    } catch {
      if (generation !== mutationGeneration.current) return;
      toast.error(t("common.requestFailed"));
    } finally {
      if (generation === mutationGeneration.current) setBusy(false);
      mutationInFlight.current = false;
    }
  }

  async function toggleCategory(category: NoteCategory) {
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    const generation = ++mutationGeneration.current;
    setBusy(true);
    try {
      if (category.lifecycle_status === "active") await notesApi.trashCategory(category);
      else await notesApi.restoreCategory(category);
      if (generation !== mutationGeneration.current) return;
      onChanged();
    } catch {
      if (generation !== mutationGeneration.current) return;
      toast.error(t("notes.categoryTrashFailed"));
    } finally {
      if (generation === mutationGeneration.current) setBusy(false);
      mutationInFlight.current = false;
    }
  }

  return <div className="flex flex-col gap-4"><FieldGroup><Field><FieldLabel htmlFor="category-name">{editing ? t("notes.renameCategory") : t("notes.newCategory")}</FieldLabel><div className="flex gap-2"><Input id="category-name" value={name} onChange={(event) => setName(event.target.value)} /><Button disabled={!name.trim() || busy} onClick={createOrRename}>{editing ? t("notes.save") : <><FolderPlus data-icon="inline-start" />{t("notes.add")}</>}</Button></div></Field></FieldGroup><div className="flex max-h-72 flex-col gap-2 overflow-y-auto">{[...active, ...trashed].map((category) => <div key={category.category_id} className="flex items-center justify-between gap-2 rounded-md border p-2"><span className="truncate">{category.name}</span><div className="flex gap-1">{category.lifecycle_status === "active" && <Button variant="ghost" size="sm" disabled={busy} onClick={() => { setEditing(category); setName(category.name); }}>{t("notes.rename")}</Button>}<Button variant="outline" size="sm" disabled={busy} onClick={() => void toggleCategory(category)}>{t(category.lifecycle_status === "active" ? "notes.trashCategory" : "notes.restoreCategory")}</Button></div></div>)}</div></div>;
}
