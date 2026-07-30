import { ArrowRight, FileText, Route, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import { serverMessage } from "../../shared/product-ui";
import type { ReadinessState } from "./types";

type SetupRecoveryRoute =
  | "/admin/projects"
  | "/admin/models"
  | "/admin/document-library";

type SetupRecoveryItem = {
  blocker: string;
  cta: string;
  description: string;
  icon: React.ReactNode;
  route: SetupRecoveryRoute;
  title: string;
};

export function SetupRecoveryCard({
  readiness,
  canAct,
  onNavigate,
}: {
  readiness: ReadinessState | null;
  canAct: boolean;
  onNavigate: (route: SetupRecoveryRoute) => void;
}) {
  const { t } = useTranslation();
  const items = setupRecoveryItems(readiness, t);

  if (items.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("recovery.title")}</CardTitle>
        <CardDescription>
          {canAct ? t("recovery.description") : t("recovery.operatorDescription")}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {items.map((item) => (
          <div key={`${item.route}-${item.blocker}`} className="rounded-md border p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex min-w-0 gap-3">
                <div className="mt-0.5 text-muted-foreground">{item.icon}</div>
                <div className="min-w-0">
                  <div className="font-medium">{item.title}</div>
                  <div className="text-sm text-muted-foreground">{item.description}</div>
                </div>
              </div>
              {canAct ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onNavigate(item.route)}
                >
                  {item.cta}
                  <ArrowRight data-icon="inline-end" />
                </Button>
              ) : (
                <Badge variant="outline">{t("recovery.adminAction")}</Badge>
              )}
            </div>
            <div className="mt-3 text-xs text-muted-foreground">
              {t("recovery.blockerLabel")}: {serverMessage(item.blocker, t)}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function setupRecoveryItems(
  readiness: ReadinessState | null,
  t: (key: string) => string,
): SetupRecoveryItem[] {
  if (!readiness || readiness.ready) {
    return [];
  }

  return readiness.setup_blockers.map((blocker) => {
    const normalized = blocker.toLowerCase();
    if (normalized.includes("membership") || normalized.includes("permission")) {
      return {
        blocker,
        cta: t("recovery.openProjects"),
        description: t("recovery.projectsDescription"),
        icon: <ShieldCheck />,
        route: "/admin/projects",
        title: t("recovery.projectsTitle"),
      };
    }
    if (
      normalized.includes("ingestion") ||
      normalized.includes("evidence") ||
      normalized.includes("document")
    ) {
      return {
        blocker,
        cta: t("recovery.openDocuments"),
        description: t("recovery.documentsDescription"),
        icon: <FileText />,
        route: "/admin/document-library",
        title: t("recovery.documentsTitle"),
      };
    }
    if (
      normalized.includes("model") ||
      normalized.includes("route") ||
      normalized.includes("credential") ||
      normalized.includes("encryption")
    ) {
      return {
        blocker,
        cta: t("recovery.openModels"),
        description: t("recovery.routeDescription"),
        icon: <Route />,
        route: "/admin/models",
        title: t("recovery.routeTitle"),
      };
    }
    return {
      blocker,
      cta: t("recovery.openProjects"),
      description: t("recovery.projectsDescription"),
      icon: <ShieldCheck />,
      route: "/admin/projects",
      title: t("recovery.projectsTitle"),
    };
  });
}
