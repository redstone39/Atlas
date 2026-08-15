"use client";

import type { Editor } from "@tiptap/react";
import { BubbleMenu } from "@tiptap/react/menus";
import { Columns3, Merge, Rows3, Split, Table2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuGroup,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "../../components/ui/context-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../../components/ui/tooltip";
import {
  clearCurrentTableCell,
  currentTableCellText,
  resetCurrentTableColumnWidths,
  selectCurrentTablePart,
} from "./note-editor-commands";

export function NoteTableControls({ editor, editable }: { editor: Editor; editable: boolean }) {
  const { t } = useTranslation();
  const pointerAnchorRef = useRef<DOMRect | null>(null);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [tooltipOpen, setTooltipOpen] = useState(false);

  useEffect(() => {
    const editorElement = editor.view?.dom;
    if (!editorElement) return;
    function rememberPointerAnchor(event: PointerEvent) {
      const target = event.target;
      pointerAnchorRef.current = target instanceof Element && target.closest("table")
        ? new DOMRect(event.clientX, event.clientY, 0, 0)
        : null;
    }

    function useSelectionAnchor() {
      pointerAnchorRef.current = null;
    }

    editorElement.addEventListener("pointerdown", rememberPointerAnchor, true);
    editorElement.addEventListener("keydown", useSelectionAnchor, true);
    return () => {
      editorElement.removeEventListener("pointerdown", rememberPointerAnchor, true);
      editorElement.removeEventListener("keydown", useSelectionAnchor, true);
    };
  }, [editor]);

  useEffect(() => {
    if (editable) return;
    setActionsOpen(false);
    setTooltipOpen(false);
  }, [editable]);

  const getReferencedVirtualElement = useCallback(() => {
    const pointerAnchor = pointerAnchorRef.current;
    return pointerAnchor
      ? {
          getBoundingClientRect: () => pointerAnchor,
        }
      : null;
  }, [editor]);
  const openTableActions = useCallback((target: HTMLButtonElement, clientX?: number, clientY?: number) => {
    const rect = target.getBoundingClientRect();
    target.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      clientX: clientX ?? rect.left + rect.width / 2,
      clientY: clientY ?? rect.top + rect.height / 2,
    }));
  }, []);

  async function copyCell() {
    try {
      await navigator.clipboard.writeText(currentTableCellText(editor));
      toast.success(t("notes.cellCopied"));
    } catch {
      toast.error(t("notes.clipboardFailed"));
    }
  }

  return (
    <ContextMenu
      open={editable && actionsOpen}
      onOpenChange={(open) => {
        const nextOpen = editable && open;
        setActionsOpen(nextOpen);
        if (nextOpen) setTooltipOpen(false);
      }}
    >
      <BubbleMenu
        editor={editor}
        pluginKey="note-table-controls"
        updateDelay={0}
        getReferencedVirtualElement={getReferencedVirtualElement}
        shouldShow={({ editor: currentEditor }) => editable && currentEditor.isActive("table")}
        options={{ placement: "top", offset: 8, flip: true, shift: true }}
      >
        <TooltipProvider delayDuration={400}>
          <Tooltip open={tooltipOpen} onOpenChange={setTooltipOpen}>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <ContextMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="secondary"
                    size="icon-sm"
                    className="size-11 shadow-sm sm:size-8"
                    aria-label={t("notes.tableActions")}
                    aria-haspopup="menu"
                    aria-expanded={actionsOpen}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={(event) => openTableActions(event.currentTarget, event.clientX, event.clientY)}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      openTableActions(event.currentTarget);
                    }}
                  >
                    <Table2 />
                  </Button>
                </ContextMenuTrigger>
              </span>
            </TooltipTrigger>
            <TooltipContent side="top">{t("notes.openTableActions")}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </BubbleMenu>
      <ContextMenuContent
        collisionPadding={8}
        className="max-h-[min(28rem,calc(100vh-1rem))]"
      >
        <ContextMenuGroup>
          <ContextMenuLabel>{t("notes.tableRows")}</ContextMenuLabel>
          <ContextMenuItem onSelect={() => editor.chain().focus().addRowBefore().run()}><Rows3 />{t("notes.addRowBefore")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => editor.chain().focus().addRowAfter().run()}><Rows3 />{t("notes.addRowAfter")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => selectCurrentTablePart(editor, "row")}><Rows3 />{t("notes.selectRow")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => editor.chain().focus().deleteRow().run()}><Rows3 />{t("notes.deleteRow")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => editor.chain().focus().toggleHeaderRow().run()}><Rows3 />{t("notes.toggleHeaderRow")}</ContextMenuItem>
        </ContextMenuGroup>
        <ContextMenuSeparator />
        <ContextMenuGroup>
          <ContextMenuLabel>{t("notes.tableColumns")}</ContextMenuLabel>
          <ContextMenuItem onSelect={() => editor.chain().focus().addColumnBefore().run()}><Columns3 />{t("notes.addColumnBefore")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => editor.chain().focus().addColumnAfter().run()}><Columns3 />{t("notes.addColumnAfter")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => selectCurrentTablePart(editor, "column")}><Columns3 />{t("notes.selectColumn")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => editor.chain().focus().deleteColumn().run()}><Columns3 />{t("notes.deleteColumn")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => editor.chain().focus().toggleHeaderColumn().run()}><Columns3 />{t("notes.toggleHeaderColumn")}</ContextMenuItem>
        </ContextMenuGroup>
        <ContextMenuSeparator />
        <ContextMenuGroup>
          <ContextMenuLabel>{t("notes.tableCells")}</ContextMenuLabel>
          <ContextMenuItem onSelect={() => editor.chain().focus().mergeCells().run()}><Merge />{t("notes.mergeCells")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => editor.chain().focus().splitCell().run()}><Split />{t("notes.splitCell")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => editor.chain().focus().toggleHeaderCell().run()}><Table2 />{t("notes.toggleHeaderCell")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => void copyCell()}><Table2 />{t("notes.copyCell")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => clearCurrentTableCell(editor)}><Table2 />{t("notes.clearCell")}</ContextMenuItem>
        </ContextMenuGroup>
        <ContextMenuSeparator />
        <ContextMenuGroup>
          <ContextMenuLabel>{t("notes.tableLayout")}</ContextMenuLabel>
          <ContextMenuItem onSelect={() => selectCurrentTablePart(editor, "table")}><Table2 />{t("notes.selectTable")}</ContextMenuItem>
          <ContextMenuItem onSelect={() => resetCurrentTableColumnWidths(editor)}><Columns3 />{t("notes.resetColumnWidths")}</ContextMenuItem>
          <ContextMenuItem disabled>{t("notes.tableKeyboardHint")}</ContextMenuItem>
          <ContextMenuItem disabled>{t("notes.tableScrollHint")}</ContextMenuItem>
          <ContextMenuItem variant="destructive" onSelect={() => editor.chain().focus().deleteTable().run()}><Table2 />{t("notes.deleteTable")}</ContextMenuItem>
        </ContextMenuGroup>
      </ContextMenuContent>
    </ContextMenu>
  );
}
