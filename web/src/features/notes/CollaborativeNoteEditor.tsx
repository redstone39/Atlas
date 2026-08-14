"use client";

import { HocuspocusProvider } from "@hocuspocus/provider";
import Collaboration from "@tiptap/extension-collaboration";
import DragHandle from "@tiptap/extension-drag-handle-react";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import { GripVertical, ImagePlus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import * as Y from "yjs";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Spinner } from "../../components/ui/spinner";
import { cn } from "../../lib/utils";
import { notesApi } from "./api";
import { NoteBlockControls } from "./NoteBlockControls";
import { NoteEditorToolbar } from "./NoteEditorToolbar";
import { NoteImageControls } from "./NoteImageControls";
import { NoteTableControls } from "./NoteTableControls";
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
  const imageInputRef = useRef<HTMLInputElement>(null);
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

      <NoteEditorToolbar
        editor={editor}
        editable={editable}
        onPickImage={() => imageInputRef.current?.click()}
      />
      <input
        ref={imageInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        hidden
        aria-label={t("notes.insertImage")}
        onChange={(event) => {
          const files = Array.from(event.currentTarget.files ?? []);
          if (editor && files.length > 0) uploadFiles(editor, files, editor.state.selection.from);
          event.currentTarget.value = "";
        }}
      />
      {uploading > 0 && <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status"><Spinner />{t("notes.imageUploading")}</div>}
      {failedUploads.map((pending) => (
        <Alert key={pending.id} variant="destructive">
          <ImagePlus />
          <AlertTitle>{pending.attachment ? t("notes.imageInsertDeferred") : t("notes.imageUploadFailed")}</AlertTitle>
          <AlertDescription><Button type="button" variant="outline" size="sm" disabled={!editable || uploading > 0} onClick={() => retryImage(pending)}>{t("admin.retry")}</Button></AlertDescription>
        </Alert>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <NoteBlockControls
          editor={editor}
          editable={editable}
          generateBlockId={() => clientIdentity("block")}
        />
        <NoteImageControls editor={editor} noteId={note.note_id} editable={editable} />
      </div>
      <div className="relative">
        {editor && <NoteTableControls editor={editor} editable={editable} />}
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
