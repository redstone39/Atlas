import type { Editor } from "@tiptap/react";
import { Bold, ChevronDown, Code, CodeXml, CornerDownLeft, ImagePlus, Italic, List, ListChecks, ListOrdered, Minus, Pilcrow, Plus, Quote, Redo2, Strikethrough, Table2, Underline as UnderlineIcon, Undo2 } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

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
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../../components/ui/tooltip";
import { NoteFindReplace } from "./NoteFindReplace";
import { NoteLinkPopover } from "./NoteLinkPopover";

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

export function NoteEditorToolbar({
  editor,
  editable,
  onPickImage,
}: {
  editor: Editor | null;
  editable: boolean;
  onPickImage: () => void;
}) {
  const { t } = useTranslation();

  return (
    <TooltipProvider delayDuration={400}>
      <div className="flex flex-wrap gap-2 rounded-md border p-1 sm:gap-1" role="toolbar" aria-label={t("notes.editorToolbar")}>
        <div className="flex gap-2 sm:gap-1" role="group" aria-label={t("notes.textStyle")}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="ghost" size="sm" disabled={!editable}>
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
                <DropdownMenuRadioGroup value={editor?.isActive("heading", { level: 1 }) ? "heading1" : editor?.isActive("heading", { level: 2 }) ? "heading2" : editor?.isActive("heading", { level: 3 }) ? "heading3" : "paragraph"}>
                  {[
                    { value: "paragraph", label: t("notes.paragraph"), icon: Pilcrow, run: () => editor?.chain().focus().setParagraph().run() },
                    { value: "heading1", label: t("notes.heading1"), icon: Pilcrow, run: () => editor?.chain().focus().setHeading({ level: 1 }).run() },
                    { value: "heading2", label: t("notes.heading2"), icon: Pilcrow, run: () => editor?.chain().focus().setHeading({ level: 2 }).run() },
                    { value: "heading3", label: t("notes.heading3"), icon: Pilcrow, run: () => editor?.chain().focus().setHeading({ level: 3 }).run() },
                  ].map(({ value, label, icon: Icon, run }) => (
                    <DropdownMenuRadioItem key={value} value={value} onSelect={run}><Icon />{label}</DropdownMenuRadioItem>
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
          <NoteLinkPopover editor={editor} editable={editable} />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="ghost" size="sm" disabled={!editable}>
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
              <Button type="button" variant="ghost" size="sm" disabled={!editable}>
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
              <Button type="button" variant="ghost" size="sm" disabled={!editable}>
                <Plus data-icon="inline-start" />{t("notes.insert")}<ChevronDown data-icon="inline-end" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuGroup>
                <DropdownMenuLabel>{t("notes.insert")}</DropdownMenuLabel>
                <DropdownMenuItem onSelect={() => editor?.chain().focus().setHardBreak().run()}><CornerDownLeft />{t("notes.hardBreak")}</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => editor?.chain().focus().setHorizontalRule().run()}><Minus />{t("notes.horizontalRule")}</DropdownMenuItem>
                <DropdownMenuItem onSelect={onPickImage}><ImagePlus />{t("notes.insertImage")}</DropdownMenuItem>
                <DropdownMenuItem disabled={Boolean(editor?.isActive("table"))} onSelect={() => editor?.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}><Table2 />{t("notes.insertTable")}</DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <div className="flex gap-2 sm:gap-1" role="group" aria-label={t("notes.historyControls")}>
          <ToolbarIconButton label={t("notes.undo")} disabled={!editable} onClick={() => editor?.chain().focus().undo().run()}><Undo2 /></ToolbarIconButton>
          <ToolbarIconButton label={t("notes.redo")} disabled={!editable} onClick={() => editor?.chain().focus().redo().run()}><Redo2 /></ToolbarIconButton>
          <NoteFindReplace editor={editor} editable={editable} />
        </div>
      </div>
    </TooltipProvider>
  );
}
