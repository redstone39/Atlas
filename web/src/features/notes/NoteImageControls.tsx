import type { Editor } from "@tiptap/react";
import { Copy, Download, ImageIcon, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import { Field, FieldGroup, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "../../components/ui/popover";
import { selectedNoteImage } from "./note-editor-commands";

function attachmentContentUrl(noteId: string, attachmentRef: string) {
  return `/api/v1/notes/${encodeURIComponent(noteId)}/attachments/${encodeURIComponent(attachmentRef)}/content`;
}

async function attachmentBlob(noteId: string, attachmentRef: string) {
  const response = await fetch(attachmentContentUrl(noteId, attachmentRef), { credentials: "same-origin" });
  if (!response.ok) throw new Error("Attachment unavailable");
  return response.blob();
}

export function NoteImageControls({ editor, noteId, editable }: { editor: Editor | null; noteId: string; editable: boolean }) {
  const { t } = useTranslation();
  const [, setSelectionVersion] = useState(0);
  const [open, setOpen] = useState(false);
  const [alt, setAlt] = useState("");
  const [caption, setCaption] = useState("");

  useEffect(() => {
    if (!editor) return;
    const update = () => setSelectionVersion((current) => current + 1);
    editor.on("selectionUpdate", update);
    editor.on("transaction", update);
    return () => {
      editor.off("selectionUpdate", update);
      editor.off("transaction", update);
    };
  }, [editor]);

  const image = editor ? selectedNoteImage(editor) : null;
  if (!image) return null;
  const selectedImage = image;

  function changeOpen(next: boolean) {
    setOpen(next);
    if (next) {
      setAlt(selectedImage.alt);
      setCaption(selectedImage.caption);
    }
  }

  function saveProperties() {
    if (!editor || !editable) return;
    editor.chain().focus().updateAttributes("noteImage", { alt, caption }).run();
    setOpen(false);
    toast.success(t("notes.imagePropertiesSaved"));
  }

  function removeImage() {
    if (!editor || !editable) return;
    editor.chain().focus().deleteSelection().run();
    setOpen(false);
  }

  async function copyImage() {
    try {
      const blob = await attachmentBlob(noteId, selectedImage.attachmentRef);
      if (!navigator.clipboard.write || typeof ClipboardItem === "undefined") throw new Error("Image clipboard unavailable");
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
      toast.success(t("notes.imageCopied"));
    } catch {
      toast.error(t("notes.imageCopyFailed"));
    }
  }

  async function downloadImage() {
    try {
      const blob = await attachmentBlob(noteId, selectedImage.attachmentRef);
      const extension = blob.type === "image/png" ? "png" : blob.type === "image/webp" ? "webp" : "jpg";
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `note-image-${selectedImage.attachmentRef}.${extension}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error(t("notes.imageDownloadFailed"));
    }
  }

  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <Button type="button" size="sm" variant="outline">
          <ImageIcon data-icon="inline-start" />{t("notes.imageProperties")}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80">
        <PopoverHeader>
          <PopoverTitle>{t("notes.imageProperties")}</PopoverTitle>
          <PopoverDescription>{t("notes.imagePropertiesDescription")}</PopoverDescription>
        </PopoverHeader>
        <FieldGroup className="gap-4">
          <Field>
            <FieldLabel htmlFor="note-image-alt">{t("notes.imageAlt")}</FieldLabel>
            <Input id="note-image-alt" value={alt} maxLength={2000} disabled={!editable} onChange={(event) => setAlt(event.target.value)} />
          </Field>
          <Field>
            <FieldLabel htmlFor="note-image-caption">{t("notes.imageCaption")}</FieldLabel>
            <Input id="note-image-caption" value={caption} maxLength={10000} disabled={!editable} onChange={(event) => setCaption(event.target.value)} />
          </Field>
        </FieldGroup>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" disabled={!editable} onClick={saveProperties}><Save data-icon="inline-start" />{t("notes.saveImageProperties")}</Button>
          <Button type="button" size="sm" variant="outline" onClick={() => void copyImage()}><Copy data-icon="inline-start" />{t("notes.copyImage")}</Button>
          <Button type="button" size="sm" variant="outline" onClick={() => void downloadImage()}><Download data-icon="inline-start" />{t("notes.downloadImage")}</Button>
          <Button type="button" size="sm" variant="destructive" disabled={!editable} onClick={removeImage}><Trash2 data-icon="inline-start" />{t("notes.removeImage")}</Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
