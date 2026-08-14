"use client";

import type { Editor } from "@tiptap/react";
import { BubbleMenu } from "@tiptap/react/menus";
import { Columns3, Merge, Rows3, Split, Table2 } from "lucide-react";
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
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../../components/ui/tooltip";
import {
  clearCurrentTableCell,
  currentTableCellText,
  resetCurrentTableColumnWidths,
  selectCurrentTablePart,
} from "./note-editor-commands";

export function NoteTableControls({ editor, editable }: { editor: Editor; editable: boolean }) {
  const { t } = useTranslation();

  async function copyCell() {
    try {
      await navigator.clipboard.writeText(currentTableCellText(editor));
      toast.success(t("notes.cellCopied"));
    } catch {
      toast.error(t("notes.clipboardFailed"));
    }
  }

  return (
    <BubbleMenu
      editor={editor}
      pluginKey="note-table-controls"
      updateDelay={0}
      shouldShow={({ editor: currentEditor }) => editable && currentEditor.isActive("table")}
      options={{ placement: "top", offset: 8, flip: true, shift: true }}
    >
      <TooltipProvider delayDuration={400}>
        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="secondary"
                    size="icon-sm"
                    className="size-11 shadow-sm sm:size-8"
                    aria-label={t("notes.tableActions")}
                    onMouseDown={(event) => event.preventDefault()}
                  >
                    <Table2 />
                  </Button>
                </DropdownMenuTrigger>
              </span>
            </TooltipTrigger>
            <TooltipContent side="top">{t("notes.openTableActions")}</TooltipContent>
          </Tooltip>
          <DropdownMenuContent align="center">
            <DropdownMenuGroup>
              <DropdownMenuLabel>{t("notes.tableRows")}</DropdownMenuLabel>
              <DropdownMenuItem onSelect={() => editor.chain().focus().addRowBefore().run()}><Rows3 />{t("notes.addRowBefore")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => editor.chain().focus().addRowAfter().run()}><Rows3 />{t("notes.addRowAfter")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => selectCurrentTablePart(editor, "row")}><Rows3 />{t("notes.selectRow")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => editor.chain().focus().deleteRow().run()}><Rows3 />{t("notes.deleteRow")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => editor.chain().focus().toggleHeaderRow().run()}><Rows3 />{t("notes.toggleHeaderRow")}</DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuLabel>{t("notes.tableColumns")}</DropdownMenuLabel>
              <DropdownMenuItem onSelect={() => editor.chain().focus().addColumnBefore().run()}><Columns3 />{t("notes.addColumnBefore")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => editor.chain().focus().addColumnAfter().run()}><Columns3 />{t("notes.addColumnAfter")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => selectCurrentTablePart(editor, "column")}><Columns3 />{t("notes.selectColumn")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => editor.chain().focus().deleteColumn().run()}><Columns3 />{t("notes.deleteColumn")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => editor.chain().focus().toggleHeaderColumn().run()}><Columns3 />{t("notes.toggleHeaderColumn")}</DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuLabel>{t("notes.tableCells")}</DropdownMenuLabel>
              <DropdownMenuItem onSelect={() => editor.chain().focus().mergeCells().run()}><Merge />{t("notes.mergeCells")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => editor.chain().focus().splitCell().run()}><Split />{t("notes.splitCell")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => editor.chain().focus().toggleHeaderCell().run()}><Table2 />{t("notes.toggleHeaderCell")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void copyCell()}><Table2 />{t("notes.copyCell")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => clearCurrentTableCell(editor)}><Table2 />{t("notes.clearCell")}</DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuLabel>{t("notes.tableLayout")}</DropdownMenuLabel>
              <DropdownMenuItem onSelect={() => selectCurrentTablePart(editor, "table")}><Table2 />{t("notes.selectTable")}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => resetCurrentTableColumnWidths(editor)}><Columns3 />{t("notes.resetColumnWidths")}</DropdownMenuItem>
              <DropdownMenuItem disabled>{t("notes.tableKeyboardHint")}</DropdownMenuItem>
              <DropdownMenuItem disabled>{t("notes.tableScrollHint")}</DropdownMenuItem>
              <DropdownMenuItem variant="destructive" onSelect={() => editor.chain().focus().deleteTable().run()}><Table2 />{t("notes.deleteTable")}</DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </TooltipProvider>
    </BubbleMenu>
  );
}
