"use client";

import { HocuspocusProvider } from "@hocuspocus/provider";
import Collaboration from "@tiptap/extension-collaboration";
import DragHandle from "@tiptap/extension-drag-handle-react";
import { Selection } from "@tiptap/pm/state";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import { Bold, ChevronDown, Code, CodeXml, Columns3, CornerDownLeft, GripVertical, Heading1, Heading2, Heading3, ImagePlus, Italic, Link2, List, ListChecks, ListOrdered, Merge, Minus, Pilcrow, Plus, Quote, Redo2, Rows3, Split, Strikethrough, Table2, Underline as UnderlineIcon, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import * as Y from "yjs";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { Spinner } from "../../components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../../components/ui/tooltip";
import { cn } from "../../lib/utils";
import { notesApi } from "./api";
import { isAllowedNoteLink } from "./link-policy";
import {
  addImageUploadPlaceholder,
  ensureTopLevelBlockIds,
  findImageUploadPlaceholder,
  noteExtensions,
  removeImageUploadPlaceholder,
} from "./note-extensions";
import type { CollaborationTicket, NoteAttachment, NoteDetail, NotesConnectionState } from "./types";

const TERMINAL_CONNECTION_STATE: Partial<Record<NotesConnectionState, true>> = {
  authentication_failed: true,
  access_revoked: true,
  save_failed: true,
};

function noteImageNode(attachment: NoteAttachment) {
  return {
    type: "noteImage",
    attrs: {
      attachment_ref: attachment.attachment_ref,
      alt: "",
      caption: "",
      width: attachment.width,
      height: attachment.height,
    },
  };
}

function clientIdentity(prefix: string) {
  if (typeof globalThis.crypto !== "undefined" && typeof globalThis.crypto.randomUUID === "function") {
    return `${prefix}-${globalThis.crypto.randomUUID()}`;
  }
  if (typeof globalThis.crypto !== "undefined" && typeof globalThis.crypto.getRandomValues === "function") {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    return `${prefix}-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export interface PendingImageUpload {
  id: string;
  key: string;
  file: File;
  fallbackPosition: number;
  attachment?: NoteAttachment;
}

export function uploadPendingNoteImage(note: NoteDetail, pending: PendingImageUpload) {
  return pending.attachment
    ? Promise.resolve(pending.attachment)
    : notesApi.uploadAttachment(note, pending.file, pending.key);
}

export function topLevelImageInsertionPosition(editor: Editor, requestedPosition: number) {
  const position = Math.max(0, Math.min(requestedPosition, editor.state.doc.content.size));
  const resolved = editor.state.doc.resolve(position);
  return resolved.depth === 0 ? position : resolved.after(1);
}

function selectedTopLevelBlockPosition(editor: Editor) {
  const index = editor.state.selection.$from.index(0);
  let position = 0;
  for (let current = 0; current < index; current += 1) {
    position += editor.state.doc.child(current).nodeSize;
  }
  return position;
}

function ToolbarIconButton({
  label,
  active = false,
  disabled,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant={active ? "secondary" : "ghost"}
          size="icon-sm"
          className="size-11 sm:size-8"
          aria-label={label}
          aria-pressed={active}
          disabled={disabled}
          onMouseDown={(event) => event.preventDefault()}
          onClick={onClick}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}

export function CollaborativeNoteEditor({ note }: { note: NoteDetail }) {
  const { t } = useTranslation();
  const document = useMemo(() => new Y.Doc({ gc: false }), [note.note_id, note.collaboration_epoch]);
  const [connectionState, setConnectionState] = useState<NotesConnectionState>("connecting");
  const [providerReadOnly, setProviderReadOnly] = useState(true);
  const [syncedDocument, setSyncedDocument] = useState<Y.Doc | null>(null);
  const [idsReadyDocument, setIdsReadyDocument] = useState<Y.Doc | null>(null);
  const [uploading, setUploading] = useState(0);
  const [failedUploads, setFailedUploads] = useState<PendingImageUpload[]>([]);
  const idsEnabled = syncedDocument === document;
  const rememberFailedUpload = useCallback((pending: PendingImageUpload) => {
    setFailedUploads((current) => [...current.filter((item) => item.id !== pending.id), pending]);
  }, []);
  const clearFailedUpload = useCallback((id: string) => {
    setFailedUploads((current) => current.filter((item) => item.id !== id));
  }, []);
  const attemptUpload = useCallback((targetEditor: Editor, pending: PendingImageUpload) => {
    if (note.lifecycle_status !== "active") return;
    void (async () => {
      setUploading((value) => value + 1);
      clearFailedUpload(pending.id);
      try {
        const attachment = await uploadPendingNoteImage(note, pending);
        const insertion = targetEditor.isDestroyed
          ? null
          : findImageUploadPlaceholder(targetEditor, pending.id);
        if (targetEditor.isDestroyed || !targetEditor.isEditable || insertion === null) {
          rememberFailedUpload({ ...pending, attachment });
          toast.error(t("notes.imageInsertDeferred"));
          return;
        }
        const inserted = targetEditor.chain().focus()
          .insertContentAt(insertion, noteImageNode(attachment)).run();
        if (!inserted) {
          rememberFailedUpload({ ...pending, attachment });
          toast.error(t("notes.imageInsertDeferred"));
          return;
        }
        removeImageUploadPlaceholder(targetEditor, pending.id);
        clearFailedUpload(pending.id);
      } catch {
        rememberFailedUpload(pending);
        toast.error(t("notes.imageUploadFailed"));
      } finally {
        setUploading((value) => Math.max(0, value - 1));
      }
    })();
  }, [clearFailedUpload, note, rememberFailedUpload, t]);
  const uploadFiles = useCallback((targetEditor: Editor, files: File[], position: number) => {
    if (note.lifecycle_status !== "active" || !targetEditor.isEditable) return;
    const topLevelPosition = topLevelImageInsertionPosition(targetEditor, position);
    for (const file of files) {
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
        toast.error(t("notes.imageUnsupported"));
        continue;
      }
      if (file.size <= 0 || file.size > 16 * 1024 * 1024) {
        toast.error(t("notes.imageTooLarge"));
        continue;
      }
      const pending: PendingImageUpload = {
        id: clientIdentity("placeholder"),
        key: clientIdentity("note-image"),
        file,
        fallbackPosition: topLevelPosition,
      };
      addImageUploadPlaceholder(targetEditor, pending.id, topLevelPosition);
      attemptUpload(targetEditor, pending);
    }
  }, [attemptUpload, note.lifecycle_status, t]);
  const editor = useEditor(
    {
      immediatelyRender: false,
      editable: false,
      editorProps: {
        attributes: {
          "aria-label": t("notes.editor"),
          class: "p-4 ps-12",
        },
        handlePaste: (_view, event) => {
          const files = Array.from(event.clipboardData?.files ?? []);
          if (files.some((file) => !["image/png", "image/jpeg", "image/webp"].includes(file.type))) {
            toast.error(t("notes.imageUnsupported"));
            return true;
          }
          if (files.length === 0) {
            const html = event.clipboardData?.getData("text/html") ?? "";
            const text = event.clipboardData?.getData("text/plain")?.trim() ?? "";
            if (/<img\b/i.test(html) || /^https?:\/\/\S+\.(png|jpe?g|webp)(?:[?#].*)?$/i.test(text)) {
              toast.error(t("notes.imageBytesRequired"));
              return true;
            }
          }
          return false;
        },
      },
      extensions: [
        ...noteExtensions({
          noteId: note.note_id,
          live: idsEnabled,
          onFiles: uploadFiles,
        }),
        Collaboration.configure({ document }),
      ],
    },
    [document, idsEnabled, note.note_id, t, uploadFiles],
  );
  const editorRef = useRef(editor);
  const blockPointerY = useRef<number | null>(null);
  editorRef.current = editor;

  useEffect(() => {
    let disposed = false;
    let collaborationProvider: HocuspocusProvider | null = null;
    setConnectionState("connecting");
    setProviderReadOnly(true);
    editorRef.current?.setEditable(false);

    const stopWithTerminalState = (state: NotesConnectionState) => {
      if (disposed) return;
      setConnectionState((current) =>
        TERMINAL_CONNECTION_STATE[current] ? current : state,
      );
      editorRef.current?.setEditable(false);
      collaborationProvider?.destroy();
    };
    notesApi.collaborationTicket(note.note_id).then((ticket) => {
      if (disposed) return;
      if (ticket.read_only !== (note.lifecycle_status === "trashed")) {
        stopWithTerminalState("access_revoked");
        return;
      }
      setProviderReadOnly(ticket.read_only);
      let pendingTicket: CollaborationTicket | null = ticket;
      collaborationProvider = new HocuspocusProvider({
        document,
        url: ticket.websocket_url,
        name: ticket.room_name,
        token: async () => {
          const currentTicket =
            pendingTicket ?? await notesApi.collaborationTicket(note.note_id);
          pendingTicket = null;
          if (
            currentTicket.collaboration_epoch !== note.collaboration_epoch ||
            currentTicket.room_name !== ticket.room_name ||
            currentTicket.websocket_url !== ticket.websocket_url ||
            currentTicket.read_only !== ticket.read_only
          ) {
            stopWithTerminalState("access_revoked");
            throw new Error("Notes collaboration authorization changed");
          }
          return currentTicket.ticket;
        },
        onAuthenticated: () => {
          if (!disposed) {
            setConnectionState((current) =>
              TERMINAL_CONNECTION_STATE[current] ? current : "syncing",
            );
          }
        },
        onAuthenticationFailed: () => {
          stopWithTerminalState("authentication_failed");
        },
        onStatus: ({ status }) => {
          if (disposed) return;
          editorRef.current?.setEditable(false);
          setConnectionState((current) => {
            if (TERMINAL_CONNECTION_STATE[current]) return current;
            if (status === "connecting") {
              return current === "connecting" ? "connecting" : "reconnecting";
            }
            return status === "connected" ? "syncing" : "reconnecting";
          });
        },
        onSynced: ({ state }) => {
          if (disposed) return;
          editorRef.current?.setEditable(false);
          if (state) setSyncedDocument(document);
          setConnectionState((current) =>
            TERMINAL_CONNECTION_STATE[current]
              ? current
              : state
                ? "synced"
                : "syncing",
          );
        },
        onClose: ({ event }) => {
          if (disposed) return;
          const reason = event.reason.toLowerCase();
          if (reason.includes("revoked") || reason.includes("invalidated")) {
            stopWithTerminalState("access_revoked");
          } else if (reason.includes("persist") || reason.includes("save")) {
            stopWithTerminalState("save_failed");
          }
        },
      });
    }).catch(() => {
      stopWithTerminalState("authentication_failed");
    });

    return () => {
      editorRef.current?.setEditable(false);
      disposed = true;
      collaborationProvider?.destroy();
    };
  }, [document, note.note_id]);

  const editable =
    note.lifecycle_status === "active" &&
    !providerReadOnly &&
    connectionState === "synced" &&
    idsReadyDocument === document;
  useEffect(() => {
    if (!editor || !idsEnabled || idsReadyDocument === document) return;
    ensureTopLevelBlockIds(editor, () => clientIdentity("block"));
    setIdsReadyDocument(document);
  }, [document, editor, idsEnabled, idsReadyDocument]);
  useEffect(() => {
    editor?.setEditable(editable);
  }, [editable, editor]);

  useEffect(() => () => {
    document.destroy();
  }, [document]);

  function setLink() {
    if (!editor || !editable) return;
    const previous = editor.getAttributes("link").href as string | undefined;
    const href = window.prompt(t("notes.linkPrompt"), previous ?? "https://");
    if (href === null) return;
    if (href.trim() === "") {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
      return;
    }
    const normalizedHref =
      href.includes("@") && !href.includes(":") ? `mailto:${href.trim()}` : href.trim();
    if (!isAllowedNoteLink(normalizedHref)) {
      toast.error(t("notes.linkInvalid"));
      return;
    }
    editor.chain().focus().extendMarkRange("link").setLink({ href: normalizedHref }).run();
  }

  function moveBlock(direction: -1 | 1) {
    if (!editor || !editable) return;
    const blockPosition = selectedTopLevelBlockPosition(editor);
    editor.chain().focus().command(({ tr, dispatch }) => {
      const index = tr.doc.resolve(blockPosition).index(0);
      const node = tr.doc.nodeAt(blockPosition);
      if (!node || (direction < 0 && index === 0) || (direction > 0 && index >= tr.doc.childCount - 1)) return false;
      let destination = blockPosition;
      if (direction < 0) {
        let target = 0;
        for (let current = 0; current < index - 1; current += 1) target += tr.doc.child(current).nodeSize;
        destination = target;
        tr.delete(blockPosition, blockPosition + node.nodeSize).insert(target, node);
      } else {
        const next = tr.doc.child(index + 1);
        destination = blockPosition + next.nodeSize;
        tr.delete(blockPosition, blockPosition + node.nodeSize).insert(destination, node);
      }
      const selectionPosition = Math.min(destination + 1, tr.doc.content.size);
      tr.setSelection(Selection.near(tr.doc.resolve(selectionPosition)));
      dispatch?.(tr.scrollIntoView());
      return true;
    }).run();
  }

  function retryImage(pending: PendingImageUpload) {
    if (!editor || !editable) return;
    if (findImageUploadPlaceholder(editor, pending.id) === null) {
      addImageUploadPlaceholder(
        editor,
        pending.id,
        Math.min(pending.fallbackPosition, editor.state.doc.content.size),
      );
    }
    attemptUpload(editor, pending);
  }

  function handleBlockPointerMove(event: ReactPointerEvent<HTMLButtonElement>) {
    if (blockPointerY.current === null || !editable) return;
    const delta = event.clientY - blockPointerY.current;
    if (Math.abs(delta) < 32) return;
    moveBlock(delta > 0 ? 1 : -1);
    blockPointerY.current = event.clientY;
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2" aria-live="polite">
        <div className="flex items-center gap-2 text-sm">
          {connectionState !== "synced" && !TERMINAL_CONNECTION_STATE[connectionState] && <Spinner />}
          <span>{t(`notes.connection.${connectionState}`)}</span>
        </div>
        {note.lifecycle_status === "trashed" && (
          <span className="text-sm text-muted-foreground">{t("notes.trashedReadOnly")}</span>
        )}
      </div>

      {TERMINAL_CONNECTION_STATE[connectionState] && (
        <Alert variant="destructive">
          <AlertTitle>{t(`notes.connection.${connectionState}`)}</AlertTitle>
          <AlertDescription>{t(`notes.connectionHelp.${connectionState}`)}</AlertDescription>
        </Alert>
      )}

      <TooltipProvider delayDuration={400}>
        <div className="flex flex-wrap gap-2 rounded-md border p-1 sm:gap-1" role="toolbar" aria-label={t("notes.editorToolbar")}>
          <div className="flex gap-2 sm:gap-1" role="group" aria-label={t("notes.textStyle")}>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="ghost" size="sm" className="h-11 sm:h-8" disabled={!editable}>
                  {editor?.isActive("heading", { level: 1 })
                    ? t("notes.heading1")
                    : editor?.isActive("heading", { level: 2 })
                      ? t("notes.heading2")
                      : editor?.isActive("heading", { level: 3 })
                        ? t("notes.heading3")
                        : t("notes.paragraph")}
                  <ChevronDown data-icon="inline-end" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>{t("notes.textStyle")}</DropdownMenuLabel>
                  <DropdownMenuRadioGroup
                    value={
                      editor?.isActive("heading", { level: 1 })
                        ? "heading1"
                        : editor?.isActive("heading", { level: 2 })
                          ? "heading2"
                          : editor?.isActive("heading", { level: 3 })
                            ? "heading3"
                            : "paragraph"
                    }
                  >
                    {[
                      { value: "paragraph", label: t("notes.paragraph"), icon: Pilcrow, run: () => editor?.chain().focus().setParagraph().run() },
                      { value: "heading1", label: t("notes.heading1"), icon: Heading1, run: () => editor?.chain().focus().setHeading({ level: 1 }).run() },
                      { value: "heading2", label: t("notes.heading2"), icon: Heading2, run: () => editor?.chain().focus().setHeading({ level: 2 }).run() },
                      { value: "heading3", label: t("notes.heading3"), icon: Heading3, run: () => editor?.chain().focus().setHeading({ level: 3 }).run() },
                    ].map(({ value, label, icon: Icon, run }) => (
                      <DropdownMenuRadioItem key={value} value={value} onSelect={run}>
                        <Icon />{label}
                      </DropdownMenuRadioItem>
                    ))}
                  </DropdownMenuRadioGroup>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <div className="flex gap-2 sm:gap-1" role="group" aria-label={t("notes.inlineFormatting")}>
            {[
              { label: t("notes.bold"), icon: Bold, active: editor?.isActive("bold"), run: () => editor?.chain().focus().toggleBold().run() },
              { label: t("notes.italic"), icon: Italic, active: editor?.isActive("italic"), run: () => editor?.chain().focus().toggleItalic().run() },
              { label: t("notes.underline"), icon: UnderlineIcon, active: editor?.isActive("underline"), run: () => editor?.chain().focus().toggleUnderline().run() },
            ].map(({ label, icon: Icon, active, run }) => (
              <ToolbarIconButton key={label} label={label} active={Boolean(active)} disabled={!editable} onClick={run}><Icon /></ToolbarIconButton>
            ))}
            <ToolbarIconButton label={t("notes.link")} active={Boolean(editor?.isActive("link"))} disabled={!editable} onClick={setLink}><Link2 /></ToolbarIconButton>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="ghost" size="sm" className="h-11 sm:h-8" disabled={!editable}>
                  {t("notes.moreFormatting")}<ChevronDown data-icon="inline-end" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>{t("notes.moreFormatting")}</DropdownMenuLabel>
                  {[
                    { label: t("notes.strike"), icon: Strikethrough, active: editor?.isActive("strike"), run: () => editor?.chain().focus().toggleStrike().run() },
                    { label: t("notes.code"), icon: Code, active: editor?.isActive("code"), run: () => editor?.chain().focus().toggleCode().run() },
                  ].map(({ label, icon: Icon, active, run }) => (
                    <DropdownMenuCheckboxItem key={label} checked={Boolean(active)} onSelect={run}><Icon />{label}</DropdownMenuCheckboxItem>
                  ))}
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <div className="flex gap-2 sm:gap-1" role="group" aria-label={t("notes.blocksAndLists")}>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="ghost" size="sm" className="h-11 sm:h-8" disabled={!editable}>
                  <List data-icon="inline-start" />{t("notes.blocksAndLists")}<ChevronDown data-icon="inline-end" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>{t("notes.blocksAndLists")}</DropdownMenuLabel>
                  {[
                    { label: t("notes.blockquote"), icon: Quote, active: editor?.isActive("blockquote"), run: () => editor?.chain().focus().toggleBlockquote().run() },
                    { label: t("notes.codeBlock"), icon: CodeXml, active: editor?.isActive("codeBlock"), run: () => editor?.chain().focus().toggleCodeBlock().run() },
                    { label: t("notes.bulletList"), icon: List, active: editor?.isActive("bulletList"), run: () => editor?.chain().focus().toggleBulletList().run() },
                    { label: t("notes.orderedList"), icon: ListOrdered, active: editor?.isActive("orderedList"), run: () => editor?.chain().focus().toggleOrderedList().run() },
                    { label: t("notes.taskList"), icon: ListChecks, active: editor?.isActive("taskList"), run: () => editor?.chain().focus().toggleTaskList().run() },
                  ].map(({ label, icon: Icon, active, run }) => (
                    <DropdownMenuCheckboxItem key={label} checked={Boolean(active)} onSelect={run}><Icon />{label}</DropdownMenuCheckboxItem>
                  ))}
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <div className="flex gap-2 sm:gap-1" role="group" aria-label={t("notes.insert")}>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="ghost" size="sm" className="h-11 sm:h-8" disabled={!editable}>
                  <Plus data-icon="inline-start" />{t("notes.insert")}<ChevronDown data-icon="inline-end" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>{t("notes.insert")}</DropdownMenuLabel>
                  <DropdownMenuItem onSelect={() => editor?.chain().focus().setHardBreak().run()}><CornerDownLeft />{t("notes.hardBreak")}</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => editor?.chain().focus().setHorizontalRule().run()}><Minus />{t("notes.horizontalRule")}</DropdownMenuItem>
                  <DropdownMenuItem disabled={Boolean(editor?.isActive("table"))} onSelect={() => editor?.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}><Table2 />{t("notes.insertTable")}</DropdownMenuItem>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
            {editor?.isActive("table") && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button type="button" variant="secondary" size="sm" className="h-11 sm:h-8" disabled={!editable}>
                    <Table2 data-icon="inline-start" />{t("notes.table")}<ChevronDown data-icon="inline-end" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start">
                  <DropdownMenuGroup>
                    <DropdownMenuLabel>{t("notes.tableRows")}</DropdownMenuLabel>
                    <DropdownMenuItem onSelect={() => editor?.chain().focus().addRowAfter().run()}><Rows3 />{t("notes.addRow")}</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => editor?.chain().focus().deleteRow().run()}><Rows3 />{t("notes.deleteRow")}</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => editor?.chain().focus().toggleHeaderRow().run()}><Rows3 />{t("notes.toggleHeaderRow")}</DropdownMenuItem>
                  </DropdownMenuGroup>
                  <DropdownMenuSeparator />
                  <DropdownMenuGroup>
                    <DropdownMenuLabel>{t("notes.tableColumns")}</DropdownMenuLabel>
                    <DropdownMenuItem onSelect={() => editor?.chain().focus().addColumnAfter().run()}><Columns3 />{t("notes.addColumn")}</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => editor?.chain().focus().deleteColumn().run()}><Columns3 />{t("notes.deleteColumn")}</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => editor?.chain().focus().toggleHeaderColumn().run()}><Columns3 />{t("notes.toggleHeaderColumn")}</DropdownMenuItem>
                  </DropdownMenuGroup>
                  <DropdownMenuSeparator />
                  <DropdownMenuGroup>
                    <DropdownMenuLabel>{t("notes.tableCells")}</DropdownMenuLabel>
                    <DropdownMenuItem onSelect={() => editor?.chain().focus().mergeCells().run()}><Merge />{t("notes.mergeCells")}</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => editor?.chain().focus().splitCell().run()}><Split />{t("notes.splitCell")}</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => editor?.chain().focus().toggleHeaderCell().run()}><Table2 />{t("notes.toggleHeaderCell")}</DropdownMenuItem>
                  </DropdownMenuGroup>
                  <DropdownMenuSeparator />
                  <DropdownMenuGroup>
                    <DropdownMenuItem variant="destructive" onSelect={() => editor?.chain().focus().deleteTable().run()}><Table2 />{t("notes.deleteTable")}</DropdownMenuItem>
                  </DropdownMenuGroup>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>

          <div className="flex gap-2 sm:gap-1" role="group" aria-label={t("notes.historyControls")}>
            <ToolbarIconButton label={t("notes.undo")} disabled={!editable} onClick={() => editor?.chain().focus().undo().run()}><Undo2 /></ToolbarIconButton>
            <ToolbarIconButton label={t("notes.redo")} disabled={!editable} onClick={() => editor?.chain().focus().redo().run()}><Redo2 /></ToolbarIconButton>
          </div>
        </div>
      </TooltipProvider>
      {uploading > 0 && <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status"><Spinner />{t("notes.imageUploading")}</div>}
      {failedUploads.map((pending) => (
        <Alert key={pending.id} variant="destructive">
          <ImagePlus />
          <AlertTitle>{pending.attachment ? t("notes.imageInsertDeferred") : t("notes.imageUploadFailed")}</AlertTitle>
          <AlertDescription><Button type="button" variant="outline" size="sm" disabled={!editable || uploading > 0} onClick={() => retryImage(pending)}>{t("admin.retry")}</Button></AlertDescription>
        </Alert>
      ))}
      <div className="flex items-center gap-1" role="group" aria-label={t("notes.selectedBlockControls")}>
        <Button
          type="button"
          size="icon-sm"
          variant="outline"
          aria-label={t("notes.touchMoveBlock")}
          disabled={!editable}
          className="touch-none"
          onPointerDown={(event) => {
            blockPointerY.current = event.clientY;
            event.currentTarget.setPointerCapture?.(event.pointerId);
          }}
          onPointerMove={handleBlockPointerMove}
          onPointerUp={() => { blockPointerY.current = null; }}
          onPointerCancel={() => { blockPointerY.current = null; }}
        ><GripVertical /></Button>
        <Button type="button" size="sm" variant="outline" aria-label={t("notes.moveBlockUp")} disabled={!editable} onClick={() => moveBlock(-1)}>↑</Button>
        <Button type="button" size="sm" variant="outline" aria-label={t("notes.moveBlockDown")} disabled={!editable} onClick={() => moveBlock(1)}>↓</Button>
      </div>
      <div className="relative">
        {editor && connectionState === "synced" && (
          <DragHandle editor={editor}>
            <Button asChild size="icon-sm" variant="outline">
              <span
                aria-hidden="true"
                className="cursor-grab select-none active:cursor-grabbing"
                data-note-drag-handle=""
              >
                <GripVertical />
              </span>
            </Button>
          </DragHandle>
        )}
        <EditorContent
          editor={editor}
          className={cn(
            "notes-editor min-h-80 rounded-md border bg-background",
            !editable && "bg-muted/30",
          )}
        />
      </div>
    </div>
  );
}
