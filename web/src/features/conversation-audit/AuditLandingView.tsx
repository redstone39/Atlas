import { History, MessageSquareText, SearchCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/ui/button";
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from "../../components/ui/card";
import { PageHeader } from "../../shared/product-ui";
import {
  adminAgentResearchAuditRoute,
  adminAuditSectionRoute,
  type AppRoute,
} from "../../shared/routes";

export function AuditLandingView({
  onNavigate,
}: {
  onNavigate: (route: AppRoute) => void;
}) {
  const { t } = useTranslation();
  return (
    <section className="flex flex-col gap-5">
      <PageHeader title={t("audit.title")} />
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <MessageSquareText aria-hidden="true" />
            <CardTitle>{t("audit.conversationHistory")}</CardTitle>
            <CardDescription>{t("audit.conversationDirectoryDescription")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              type="button"
              onClick={() => onNavigate(adminAuditSectionRoute("conversations"))}
            >
              {t("audit.openConversationDirectory")}
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <History aria-hidden="true" />
            <CardTitle>{t("audit.operationHistory")}</CardTitle>
            <CardDescription>{t("audit.operationDirectoryDescription")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              type="button"
              onClick={() => onNavigate(adminAuditSectionRoute("events"))}
            >
              {t("audit.openOperationDirectory")}
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <SearchCheck aria-hidden="true" />
            <CardTitle>{t("agentResearchAudit.title")}</CardTitle>
            <CardDescription>{t("agentResearchAudit.landingDescription")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              type="button"
              onClick={() => onNavigate(adminAgentResearchAuditRoute())}
            >
              {t("agentResearchAudit.openDirectory")}
            </Button>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
