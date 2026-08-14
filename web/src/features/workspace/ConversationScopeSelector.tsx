import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../../components/ui/dialog";
import { Empty, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Spinner } from "../../components/ui/spinner";
import { scopeTagKey } from "./scopePersistence";
import type { DocumentTagSummary } from "./types";

export function ConversationScopeSelector({
  items,
  value,
  onValueChange,
  loading,
  disabled,
}: {
  items: DocumentTagSummary[];
  value: DocumentTagSummary[];
  onValueChange: (value: DocumentTagSummary[]) => void;
  loading: boolean;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draftValue, setDraftValue] = useState<DocumentTagSummary[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const normalizedQuery = searchQuery.trim().toLocaleLowerCase();
  const filteredItems = useMemo(
    () => items.filter((item) =>
      !normalizedQuery ||
      item.label.toLocaleLowerCase().includes(normalizedQuery) ||
      item.tag_id.toLocaleLowerCase().includes(normalizedQuery)
    ),
    [items, normalizedQuery],
  );
  const grouped = useMemo(
    () => ({
      team: filteredItems.filter((item) => item.tag_type === "team"),
      project: filteredItems.filter((item) => item.tag_type === "project"),
    }),
    [filteredItems],
  );

  function handleOpenChange(nextOpen: boolean) {
    if (nextOpen) {
      setDraftValue(value);
      setSearchQuery("");
    }
    setOpen(nextOpen);
  }

  function toggleDraftValue(item: DocumentTagSummary, checked: boolean) {
    setDraftValue((current) => {
      const itemKey = scopeTagKey(item);
      if (checked) {
        return current.some((selected) => scopeTagKey(selected) === itemKey)
          ? current
          : [...current, item];
      }
      return current.filter((selected) => scopeTagKey(selected) !== itemKey);
    });
  }

  return (
    <Field className="w-auto shrink-0 gap-0">
      <FieldLabel htmlFor="conversation-knowledge-scope-trigger" className="sr-only">
        {t("workspace.scope")}
      </FieldLabel>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogTrigger asChild>
          <Button
            id="conversation-knowledge-scope-trigger"
            type="button"
            aria-label={t("workspace.scope")}
            variant="outline"
            size="sm"
            className="max-w-48 justify-start bg-background/80"
            disabled={disabled}
          >
            {loading && <Spinner data-icon="inline-start" />}
            <span className="truncate">
              {loading
                ? t("workspace.scopeLoading")
                : value.length > 0
                  ? t("workspace.scopeSelectedCount", { count: value.length })
                  : t("workspace.allAccessible")}
            </span>
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("workspace.scopeDialogTitle")}</DialogTitle>
            <DialogDescription>{t("workspace.scopeDialogDescription")}</DialogDescription>
          </DialogHeader>
          <Field>
            <FieldLabel htmlFor="conversation-knowledge-scope-search">
              {t("workspace.scopeSearch")}
            </FieldLabel>
            <Input
              id="conversation-knowledge-scope-search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder={t("workspace.scopeSearchPlaceholder")}
            />
          </Field>
          <div className="grid max-h-72 gap-4 overflow-y-auto rounded-md border p-2">
            {items.length === 0 ? (
              <Empty className="min-h-32 border-0 p-4 md:p-6">
                <EmptyHeader><EmptyTitle>{t("workspace.scopeEmpty")}</EmptyTitle></EmptyHeader>
              </Empty>
            ) : filteredItems.length === 0 ? (
              <Empty className="min-h-32 border-0 p-4 md:p-6">
                <EmptyHeader><EmptyTitle>{t("workspace.scopeNoMatches")}</EmptyTitle></EmptyHeader>
              </Empty>
            ) : (
              (["team", "project"] as const).map((tagType) =>
                grouped[tagType].length > 0 ? (
                  <FieldSet key={tagType} className="gap-2">
                    <FieldLegend variant="label">
                      {t(tagType === "team" ? "workspace.scopeTeam" : "workspace.scopeProject")}
                    </FieldLegend>
                    {grouped[tagType].map((item) => (
                      <label
                        key={scopeTagKey(item)}
                        className="flex min-h-11 items-center gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:bg-muted/50 focus-within:bg-muted/50 focus-within:outline-none focus-within:ring-2 focus-within:ring-ring"
                      >
                        <Checkbox
                          checked={draftValue.some(
                            (selected) => scopeTagKey(selected) === scopeTagKey(item),
                          )}
                          onCheckedChange={(checked) => toggleDraftValue(item, checked === true)}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium">{item.label}</span>
                          <span className="block truncate text-xs text-muted-foreground">{item.tag_id}</span>
                        </span>
                      </label>
                    ))}
                  </FieldSet>
                ) : null
              )
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              {t("admin.cancel")}
            </Button>
            <Button
              type="button"
              onClick={() => {
                onValueChange(draftValue);
                setOpen(false);
              }}
            >
              {t("workspace.scopeApply")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Field>
  );
}
