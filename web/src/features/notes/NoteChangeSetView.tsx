import { useTranslation } from "react-i18next";

import { Badge } from "../../components/ui/badge";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import type { NoteChangeSet } from "./types";

function valueLabel(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "—";
  return JSON.stringify(value);
}
function pathLabel(path: number[]) {
  return path.length === 0 ? "/" : `/${path.join("/")}`;
}

export function NoteChangeSetView({ changeSet }: { changeSet: NoteChangeSet }) {
  const { t } = useTranslation();
  const hasChanges =
    changeSet.text.length > 0 ||
    changeSet.nodes.length > 0 ||
    changeSet.marks.length > 0 ||
    changeSet.attributes.length > 0 ||
    changeSet.moves.length > 0;

  if (!hasChanges) {
    return (
      <Empty className="min-h-24 border">
        <EmptyHeader>
          <EmptyTitle>{t("notes.diffEmptyTitle")}</EmptyTitle>
          <EmptyDescription>{t("notes.diffEmptyDescription")}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <div className="flex flex-col gap-3" aria-label={t("notes.exactChanges")}>
      {changeSet.moves.map((change) => (
        <div key={`move-${change.block_id}-${change.from_path[0]}-${change.to_path[0]}`} className="rounded-md border p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{t("notes.blockMoved")}</Badge>
            <span>{change.block_id}</span>
          </div>
          <p className="mt-2 text-muted-foreground">{t("notes.blockMovePath", { from: pathLabel(change.from_path), to: pathLabel(change.to_path) })}</p>
        </div>
      ))}
      {changeSet.text.map((change, index) => (
        <div key={`text-${index}`} className="rounded-md border p-3 text-sm">
          <Badge variant="outline">{t(`notes.change.${change.change}`)}</Badge>
          <p className="mt-2 text-muted-foreground">
            {t("notes.textChangeLocation", {
              path: pathLabel(change.path),
              from: change.from_offset,
              to: change.to_offset,
            })}
          </p>
          <dl className="mt-2 grid gap-2 sm:grid-cols-2">
            <div>
              <dt className="font-medium">{t("notes.beforeText")}</dt>
              <dd className="whitespace-pre-wrap text-muted-foreground">{change.before || "—"}</dd>
            </div>
            <div>
              <dt className="font-medium">{t("notes.afterText")}</dt>
              <dd className="whitespace-pre-wrap text-muted-foreground">{change.after || "—"}</dd>
            </div>
          </dl>
        </div>
      ))}
      {changeSet.nodes.map((change, index) => (
        <div key={`node-${index}`} className="rounded-md border p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{t("notes.nodeChange")}</Badge>
            <span>{t(`notes.change.${change.change}`)}</span>
          </div>
          <p className="mt-2 text-muted-foreground">
            {t("notes.changePath", { path: pathLabel(change.path) })}
          </p>
          <p className="mt-2 text-muted-foreground">
            {t("notes.beforeAfter", {
              before: change.before_type ?? "—",
              after: change.after_type ?? "—",
            })}
          </p>
        </div>
      ))}
      {changeSet.marks.map((change, index) => (
        <div key={`mark-${index}`} className="rounded-md border p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{t("notes.markChange")}</Badge>
            <span>{change.mark_type}</span>
            <span>{t(`notes.change.${change.change}`)}</span>
          </div>
          <p className="mt-2 text-muted-foreground">
            {t("notes.changePath", { path: pathLabel(change.path) })}
          </p>
          <p className="mt-2 text-muted-foreground">
            {t("notes.beforeAfter", {
              before: valueLabel(change.before),
              after: valueLabel(change.after),
            })}
          </p>
        </div>
      ))}
      {changeSet.attributes.map((change, index) => (
        <div key={`attribute-${index}`} className="rounded-md border p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{t("notes.attributeChange")}</Badge>
            <span>{change.node_type}.{change.attribute}</span>
          </div>
          <p className="mt-2 text-muted-foreground">
            {t("notes.changePath", { path: pathLabel(change.path) })}
          </p>
          <p className="mt-2 text-muted-foreground">
            {t("notes.beforeAfter", {
              before: valueLabel(change.before),
              after: valueLabel(change.after),
            })}
          </p>
        </div>
      ))}
    </div>
  );
}
