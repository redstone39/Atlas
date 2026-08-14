import { BookOpen, NotebookPen } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "../components/ui/button";
import { scopeNotesRoute, type AppRoute } from "./routes";

export function ScopeSecondaryNavigation({
  scopeType,
  scopeId,
  active,
  workspace = false,
  onNavigate,
}: {
  scopeType: "project" | "team";
  scopeId: string;
  active: "knowledge" | "notes";
  workspace?: boolean;
  onNavigate: (route: AppRoute) => void;
}) {
  const { t } = useTranslation();
  const family = scopeType === "project" ? "projects" : "teams";
  const knowledgeRoute = `${workspace ? "/workspace" : ""}/${family}/${encodeURIComponent(scopeId)}/knowledge` as AppRoute;

  return (
    <nav aria-label={t("notes.scopeNavigation")} className="flex flex-wrap gap-2">
      <Button
        type="button"
        variant={active === "knowledge" ? "secondary" : "ghost"}
        size="sm"
        aria-current={active === "knowledge" ? "page" : undefined}
        onClick={() => onNavigate(knowledgeRoute)}
      >
        <BookOpen data-icon="inline-start" />
        {t("notes.knowledgeTab")}
      </Button>
      <Button
        type="button"
        variant={active === "notes" ? "secondary" : "ghost"}
        size="sm"
        aria-current={active === "notes" ? "page" : undefined}
        onClick={() => onNavigate(scopeNotesRoute(scopeType, scopeId, { kind: "list" }, workspace))}
      >
        <NotebookPen data-icon="inline-start" />
        {t("notes.notesTab")}
      </Button>
    </nav>
  );
}
