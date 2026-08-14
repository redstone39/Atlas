import type { Editor } from "@tiptap/react";
import { Copy, ExternalLink, Link2, Save, Unlink } from "lucide-react";
import { useRef, useState } from "react";
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
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { isAllowedNoteLink } from "./link-policy";

interface LinkRange {
  from: number;
  to: number;
  originalText: string;
}

export function NoteLinkPopover({ editor, editable }: { editor: Editor | null; editable: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [href, setHref] = useState("");
  const [displayText, setDisplayText] = useState("");
  const rangeRef = useRef<LinkRange>({ from: 0, to: 0, originalText: "" });
  const active = Boolean(editor?.isActive("link"));

  function changeOpen(next: boolean) {
    if (!editor || !editable) return;
    if (next && editor.isActive("link")) editor.chain().focus().extendMarkRange("link").run();
    if (next) {
      const { from, to } = editor.state.selection;
      const originalText = editor.state.doc.textBetween(from, to, "");
      const currentHref = String(editor.getAttributes("link").href ?? "");
      rangeRef.current = { from, to, originalText };
      setHref(currentHref || "https://");
      setDisplayText(originalText || currentHref);
    }
    setOpen(next);
  }

  function normalizedHref() {
    const trimmed = href.trim();
    return trimmed.includes("@") && !trimmed.includes(":") ? `mailto:${trimmed}` : trimmed;
  }

  function saveLink() {
    if (!editor || !editable) return;
    const nextHref = normalizedHref();
    const nextText = displayText.trim();
    if (!nextText || !isAllowedNoteLink(nextHref)) {
      toast.error(t("notes.linkInvalid"));
      return;
    }
    const { from, to, originalText } = rangeRef.current;
    const chain = editor.chain().focus().setTextSelection({ from, to });
    if (nextText !== originalText) chain.insertContentAt({ from, to }, nextText);
    chain.setTextSelection({ from, to: from + nextText.length }).setLink({ href: nextHref }).run();
    setOpen(false);
  }

  function removeLink() {
    if (!editor || !editable) return;
    const { from, to } = rangeRef.current;
    editor.chain().focus().setTextSelection({ from, to }).unsetLink().run();
    setOpen(false);
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(normalizedHref());
      toast.success(t("notes.linkCopied"));
    } catch {
      toast.error(t("notes.clipboardFailed"));
    }
  }

  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant={active ? "secondary" : "ghost"}
              size="icon-sm"
              className="size-11 sm:size-8"
              aria-label={t("notes.link")}
              aria-pressed={active}
              disabled={!editable}
              onMouseDown={(event) => event.preventDefault()}
            ><Link2 /></Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">{t("notes.link")}</TooltipContent>
      </Tooltip>
      <PopoverContent align="start" className="w-80">
        <PopoverHeader>
          <PopoverTitle>{t("notes.editLink")}</PopoverTitle>
          <PopoverDescription>{t("notes.editLinkDescription")}</PopoverDescription>
        </PopoverHeader>
        <FieldGroup className="gap-4">
          <Field>
            <FieldLabel htmlFor="note-link-text">{t("notes.linkText")}</FieldLabel>
            <Input id="note-link-text" value={displayText} onChange={(event) => setDisplayText(event.target.value)} />
          </Field>
          <Field>
            <FieldLabel htmlFor="note-link-url">{t("notes.linkUrl")}</FieldLabel>
            <Input id="note-link-url" value={href} onChange={(event) => setHref(event.target.value)} />
          </Field>
        </FieldGroup>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" disabled={!displayText.trim()} onClick={saveLink}><Save data-icon="inline-start" />{t("notes.saveLink")}</Button>
          <Button type="button" size="sm" variant="outline" disabled={!isAllowedNoteLink(normalizedHref())} onClick={() => window.open(normalizedHref(), "_blank", "noopener,noreferrer")}><ExternalLink data-icon="inline-start" />{t("notes.openLink")}</Button>
          <Button type="button" size="sm" variant="outline" disabled={!isAllowedNoteLink(normalizedHref())} onClick={() => void copyLink()}><Copy data-icon="inline-start" />{t("notes.copyLink")}</Button>
          {active && <Button type="button" size="sm" variant="destructive" onClick={removeLink}><Unlink data-icon="inline-start" />{t("notes.removeLink")}</Button>}
        </div>
      </PopoverContent>
    </Popover>
  );
}
