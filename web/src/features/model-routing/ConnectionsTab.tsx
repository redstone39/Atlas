import { CheckCircle2, KeyRound, Pencil, Plus, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import { StatusBadge, localizedStatusLabel, serverMessage } from "../../shared/product-ui";
import type { ProviderConnectionStatus, ProviderType } from "./types";

const providerLabelKeys: Record<ProviderType, string> = {
  openai_compatible: "admin.providerOpenAICompatible",
  azure_openai: "models.providerAzure",
  anthropic: "models.providerAnthropic",
};

export function ConnectionsTab({
  connections, refreshError, pendingAction, locale, onRefresh, onCreate, onEdit, onTest,
}: {
  connections: ProviderConnectionStatus[];
  refreshError: string;
  pendingAction: string;
  locale: string;
  onRefresh: () => Promise<void>;
  onCreate: () => void;
  onEdit: (connection: ProviderConnectionStatus) => void;
  onTest: (connection: ProviderConnectionStatus) => Promise<void>;
}) {
 const { t } = useTranslation();
 return (
    <>
            {refreshError && (
              <Alert variant="destructive">
                <AlertTitle>{t("admin.listLoadFailed")}</AlertTitle>
                <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
                  <span>{serverMessage(refreshError, t)}</span>
                  <Button variant="outline" size="sm" onClick={() => void onRefresh()}>
                    {t("admin.retry")}
                  </Button>
                </AlertDescription>
              </Alert>
            )}
            <div className="flex flex-wrap justify-end gap-2">
                <Button variant="outline" onClick={() => void onRefresh()}>
                  <RefreshCw data-icon="inline-start" />
                  {t("models.refresh")}
                </Button>
                <Button onClick={onCreate}>
                  <Plus data-icon="inline-start" />
                  {t("models.addConnection")}
                </Button>
            </div>

            {connections.length === 0 ? (
              <Empty className="border">
                <EmptyHeader>
                  <EmptyTitle>{t("models.emptyConnectionsTitle")}</EmptyTitle>
                  <EmptyDescription>{t("models.emptyConnectionsDescription")}</EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <div className="grid gap-4">
                {connections.map((connection) => {
                  const credentialRequired =
                    connection.status === "credential_required" ||
                    !connection.credential_configured;
                  return (
                    <Card key={connection.connection_id}>
                      <CardHeader>
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                          <div className="min-w-0 flex flex-col gap-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <CardTitle>{connection.display_name}</CardTitle>
                              <StatusBadge
                                semantic={connectionStatusSemantic(connection.status)}
                                label={localizedStatusLabel(connection.status, t)}
                              />
                              {!connection.enabled ? (
                                <Badge variant="outline">{t("models.disabled")}</Badge>
                              ) : null}
                            </div>
                            <CardDescription className="break-all">
                              {t(providerLabelKeys[connection.provider_type])}
                              {" · "}
                              {connection.endpoint_url}
                              {connection.api_version
                                ? ` · ${t("models.apiVersion")}: ${connection.api_version}`
                                : null}
                            </CardDescription>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => onEdit(connection)}
                            >
                              {credentialRequired ? (
                                <KeyRound data-icon="inline-start" />
                              ) : (
                                <Pencil data-icon="inline-start" />
                              )}
                              {credentialRequired
                                ? t("models.setApiKey")
                                : t("models.editConnection")}
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={
                                pendingAction === `test-connection-${connection.connection_id}`
                              }
                              onClick={() =>
                                void onTest(connection)
                              }
                            >
                              <CheckCircle2 data-icon="inline-start" />
                              {t("models.testConnection")}
                            </Button>
                          </div>
                        </div>
                      </CardHeader>
                      <CardContent className="flex flex-col gap-3">
                        <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-3">
                          <div>
                            {t("models.credentialState")}: {connection.credential_configured
                              ? t("models.configured")
                              : t("models.apiKeyRequired")}
                          </div>
                          <div>
                            {t("models.lastVerified")}: {readableTime(
                              connection.last_verified_at,
                              t("models.never"),
                              locale,
                            )}
                          </div>
                          <div>{t("models.linkedModels", { count: connection.linked_model_count })}</div>
                        </div>
                        {credentialRequired ? (
                          <Alert>
                            <KeyRound />
                            <AlertTitle>{t("models.apiKeyRequired")}</AlertTitle>
                            <AlertDescription>
                              {t("models.apiKeyRequiredDescription")}
                            </AlertDescription>
                          </Alert>
                        ) : null}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}
    </>
 );
}

function readableTime(value: string | null, fallback: string, locale: string) {
  if (!value) return fallback;
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString(locale);
}

function connectionStatusSemantic(status: ProviderConnectionStatus["status"]) {
  if (status === "verified") return "success" as const;
  if (status === "configured" || status === "credential_required") return "attention" as const;
  if (status === "verification_failed") return "failure" as const;
  return "inactive" as const;
}
