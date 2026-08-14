import type { Editor } from "@tiptap/react";
import { Braces, Clipboard, Copy, GripVertical, MoreHorizontal, Pilcrow, Quote, Trash2, Type } from "lucide-react";
import { useRef, type PointerEvent as ReactPointerEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import {
  convertSelectedBlock,
  deleteSelectedTopLevelBlock,
  duplicateSelectedTopLevelBlock,
  moveSelectedTopLevelBlock,
  selectedTopLevelBlockText,
  selectSelectedTopLevelBlock,
} from "./note-editor-commands";

export function NoteBlockControls({
  editor,
  editable,
  generateBlockId,
}: {
  editor: Editor | null;
  editable: boolean;
  generateBlockId: () => string;
}) {
  const { t } = useTranslation();
  const blockPointerY = useRef<number | null>(null);

  function move(direction: -1 | 1) {
    if (editor && editable) moveSelectedTopLevelBlock(editor, direction);
  }

  function handleBlockPointerMove(event: ReactPointerEvent<HTMLButtonElement>) {
    if (blockPointerY.current === null || !editable) return;
    const delta = event.clientY - blockPointerY.current;
    if (Math.abs(delta) < 32) return;
    move(delta > 0 ? 1 : -1);
    blockPointerY.current = event.clientY;
  }

  async function copyBlockContent() {
    if (!editor || !editable) return;
    const text = selectedTopLevelBlockText(editor);
    try {
      await navigator.clipboard.writeText(text);
      toast.success(t("notes.blockContentCopied"));
    } catch {
      toast.error(t("notes.clipboardFailed"));
    }
  }

  return (
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
      <Button type="button" size="sm" variant="outline" aria-label={t("notes.moveBlockUp")} disabled={!editable} onClick={() => move(-1)}>↑</Button>
      <Button type="button" size="sm" variant="outline" aria-label={t("notes.moveBlockDown")} disabled={!editable} onClick={() => move(1)}>↓</Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" size="icon-sm" variant="outline" aria-label={t("notes.blockActions")} disabled={!editable}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuGroup>
            <DropdownMenuLabel>{t("notes.blockActions")}</DropdownMenuLabel>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger><Type />{t("notes.changeBlockType")}</DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                <DropdownMenuGroup>
                  {[
                    { key: "paragraph" as const, label: t("notes.paragraph"), icon: Pilcrow },
                    { key: "heading1" as const, label: t("notes.heading1"), icon: Type },
                    { key: "heading2" as const, label: t("notes.heading2"), icon: Type },
                    { key: "heading3" as const, label: t("notes.heading3"), icon: Type },
                    { key: "blockquote" as const, label: t("notes.blockquote"), icon: Quote },
                    { key: "codeBlock" as const, label: t("notes.codeBlock"), icon: Braces },
                  ].map(({ key, label, icon: Icon }) => (
                    <DropdownMenuItem key={key} onSelect={() => editor && convertSelectedBlock(editor, key)}>
                      <Icon />{label}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuGroup>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuItem onSelect={() => editor && duplicateSelectedTopLevelBlock(editor, generateBlockId())}>
              <Copy />{t("notes.duplicateBlock")}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void copyBlockContent()}>
              <Clipboard />{t("notes.copyBlockContent")}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => editor && selectSelectedTopLevelBlock(editor)}>
              <Type />{t("notes.selectBlock")}
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem variant="destructive" onSelect={() => editor && deleteSelectedTopLevelBlock(editor)}>
              <Trash2 />{t("notes.deleteBlock")}
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
