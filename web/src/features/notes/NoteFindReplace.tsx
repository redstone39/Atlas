import type { Editor } from "@tiptap/react";
import { ChevronDown, ChevronUp, Replace, ReplaceAll, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
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
import {
  noteSearchState,
  replaceAllNoteSearchMatches,
  replaceCurrentNoteSearchMatch,
  setNoteSearch,
  type NoteSearchState,
} from "./note-search";

const EMPTY_SEARCH: NoteSearchState = { query: "", caseSensitive: false, activeIndex: 0, matches: [] };

export function NoteFindReplace({ editor, editable }: { editor: Editor | null; editable: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [replacement, setReplacement] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [search, setSearch] = useState<NoteSearchState>(EMPTY_SEARCH);

  useEffect(() => {
    if (!editor) return;
    const synchronize = () => setSearch(noteSearchState(editor));
    editor.on("transaction", synchronize);
    return () => {
      editor.off("transaction", synchronize);
    };
  }, [editor]);

  function revealActiveMatch() {
    requestAnimationFrame(() => document.querySelector(".note-search-match-active")?.scrollIntoView({ block: "nearest" }));
  }

  function applySearch(next: { query?: string; caseSensitive?: boolean; activeIndex?: number }) {
    if (!editor) return;
    setSearch(setNoteSearch(editor, next));
    revealActiveMatch();
  }

  function changeOpen(next: boolean) {
    setOpen(next);
    if (!next && editor) {
      setQuery("");
      setSearch(setNoteSearch(editor, { query: "", activeIndex: 0 }));
    }
  }

  function changeQuery(value: string) {
    setQuery(value);
    applySearch({ query: value, caseSensitive, activeIndex: 0 });
  }

  function changeCaseSensitive(checked: boolean) {
    setCaseSensitive(checked);
    applySearch({ query, caseSensitive: checked, activeIndex: 0 });
  }

  function navigate(direction: -1 | 1) {
    if (search.matches.length === 0) return;
    applySearch({ activeIndex: search.activeIndex + direction });
  }

  function replaceCurrent() {
    if (!editor || !editable || !replaceCurrentNoteSearchMatch(editor, replacement)) return;
    setSearch(noteSearchState(editor));
    revealActiveMatch();
  }

  function replaceAll() {
    if (!editor || !editable) return;
    const count = replaceAllNoteSearchMatches(editor, replacement);
    setSearch(noteSearchState(editor));
    if (count > 0) toast.success(t("notes.replacedMatches", { count }));
  }

  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <Button type="button" size="icon-sm" variant="ghost" className="size-11 sm:size-8" aria-label={t("notes.findReplace")} disabled={!editor}>
          <Search />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <PopoverHeader>
          <PopoverTitle>{t("notes.findReplace")}</PopoverTitle>
          <PopoverDescription>{t("notes.findReplaceDescription")}</PopoverDescription>
        </PopoverHeader>
        <FieldGroup className="gap-4">
          <Field>
            <FieldLabel htmlFor="note-find">{t("notes.find")}</FieldLabel>
            <Input
              id="note-find"
              autoFocus
              value={query}
              onChange={(event) => changeQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  navigate(event.shiftKey ? -1 : 1);
                }
              }}
            />
          </Field>
          <Field orientation="horizontal">
            <Checkbox id="note-case-sensitive" checked={caseSensitive} onCheckedChange={(checked) => changeCaseSensitive(checked === true)} />
            <FieldLabel htmlFor="note-case-sensitive">{t("notes.caseSensitive")}</FieldLabel>
          </Field>
          <Field>
            <FieldLabel htmlFor="note-replace">{t("notes.replaceWith")}</FieldLabel>
            <Input id="note-replace" value={replacement} disabled={!editable} onChange={(event) => setReplacement(event.target.value)} />
          </Field>
        </FieldGroup>
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm text-muted-foreground" aria-live="polite">
            {search.matches.length === 0 ? t("notes.noMatches") : t("notes.matchCount", { current: search.activeIndex + 1, total: search.matches.length })}
          </span>
          <div className="flex gap-1">
            <Button type="button" size="icon-sm" variant="outline" aria-label={t("notes.previousMatch")} disabled={search.matches.length === 0} onClick={() => navigate(-1)}><ChevronUp /></Button>
            <Button type="button" size="icon-sm" variant="outline" aria-label={t("notes.nextMatch")} disabled={search.matches.length === 0} onClick={() => navigate(1)}><ChevronDown /></Button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" disabled={!editable || search.matches.length === 0} onClick={replaceCurrent}><Replace data-icon="inline-start" />{t("notes.replace")}</Button>
          <Button type="button" size="sm" variant="outline" disabled={!editable || search.matches.length === 0} onClick={replaceAll}><ReplaceAll data-icon="inline-start" />{t("notes.replaceAll")}</Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
