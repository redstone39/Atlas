import { Globe2, UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "../ui/card";
import { LanguageSwitch } from "../../shared/product-ui";
import { ThemeSwitch } from "../../shared/theme";
import type { SessionState } from "../../features/identity-session/index";
import { ConversationLearningSettingsFeature } from "../../features/conversation-review";
import { NotesSettingsFeature } from "../../features/notes";
import type { ManagementNavGroup } from "../../shared/navigation";
import type { AppRoute } from "../../shared/routes";

export function SettingsPage({
  session,
  managementGroups,
}: {
  session: SessionState;
  managementGroups: ManagementNavGroup[];
  onNavigate: (route: AppRoute) => void;
}) {
  const { t } = useTranslation();

  return (
    <section
      className="flex w-full flex-col gap-5"
    >
      {managementGroups.length > 0 && (
        <h2 id="settings-management" className="sr-only">
          {t("settings.management")}
        </h2>
      )}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          {t("settings.title")}
        </h1>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <UserRound data-icon="inline-start" />
              {t("settings.account")}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            <div>
              <div className="font-medium">{session.actor?.display_name}</div>
              <div className="text-muted-foreground">
                {session.actor?.issuer}
              </div>
            </div>
            <div className="rounded-md border bg-muted/30 p-3">
              <div className="text-xs uppercase text-muted-foreground">
                {t("settings.role")}
              </div>
              <div className="font-medium">
                {session.system_role ?? t("app.unknownRole")}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Globe2 data-icon="inline-start" />
              {t("settings.preferences")}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <LanguageSwitch />
            <ThemeSwitch />
          </CardContent>
        </Card>
      </div>
      {session.system_role === "admin" && (
        <>
          <ConversationLearningSettingsFeature />
          <NotesSettingsFeature />
        </>
      )}
    </section>
  );
}
