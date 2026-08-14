import { CheckCircle2, Cpu, ImageIcon, Pencil, Plus, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { LoadErrorState, LoadingState, StatusBadge, TechnicalDetails, localizedStatusLabel, serverMessage } from "../../shared/product-ui";
import type { ModelRouteStatus, ProviderConnectionStatus } from "./types";

export function ModelsTab({
 routes, connections, textDefaultRouteId, visionDefaultRouteId, loading, loadError,
 pendingAction, locale, onRefresh, onRefreshRoutes, onCreate, onEdit, onTest, onSetDefault,
}: {
 routes: ModelRouteStatus[]; connections: ProviderConnectionStatus[];
 textDefaultRouteId: string | null; visionDefaultRouteId: string | null;
 loading: boolean; loadError: string; pendingAction: string; locale: string;
 onRefresh: () => Promise<void>; onRefreshRoutes: () => Promise<void>;
 onCreate: () => void; onEdit: (route: ModelRouteStatus) => void;
 onTest: (route: ModelRouteStatus) => Promise<void>;
 onSetDefault: (route: ModelRouteStatus, capability: "text" | "vision") => Promise<void>;
}) {
 const { t } = useTranslation();
 return (
    <>
            {loading ? (
              <LoadingState
                title={t("models.loadingTitle")}
              />
            ) : loadError ? (
              <LoadErrorState
                title={t("admin.listLoadFailed")}
                description={serverMessage(loadError, t)}
                retryLabel={t("admin.retry")}
                onRetry={() => void onRefreshRoutes()}
              />
            ) : (
            <>
            <div className="flex flex-wrap justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => void onRefresh()}
                >
                  <RefreshCw data-icon="inline-start" />
                  {t("models.refresh")}
                </Button>
                <Button onClick={() => onCreate()} disabled={connections.length === 0}>
                  <Plus data-icon="inline-start" />
                  {t("models.addModel")}
                </Button>
            </div>
            <div className="flex flex-wrap gap-2" aria-label={t("models.defaultRoles")}>
              <Badge variant="outline">
                {t("models.textDefaultSelection", {
                  model:
                    routes.find((route) => route.route_id === textDefaultRouteId)
                      ?.display_name ?? t("models.notAssigned"),
                })}
              </Badge>
              <Badge variant="outline">
                {t("models.visionDefaultSelection", {
                  model:
                    routes.find((route) => route.route_id === visionDefaultRouteId)
                      ?.display_name ?? t("models.notAssigned"),
                })}
              </Badge>
            </div>


            {routes.length === 0 ? (
              <Empty className="border">
                <EmptyHeader>
                  <EmptyTitle>{t("models.emptyTitle")}</EmptyTitle>
                  <EmptyDescription>{t("models.emptyDescription")}</EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <Card>
                <CardContent className="p-0">
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>{t("models.modelOption")}</TableHead>
                          <TableHead>{t("admin.modelName")}</TableHead>
                          <TableHead>{t("models.connection")}</TableHead>
                          <TableHead>{t("users.status")}</TableHead>
                          <TableHead>{t("models.defaultRoles")}</TableHead>
                          <TableHead>{t("common.technicalDetails")}</TableHead>
                          <TableHead>{t("users.action")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {routes.map((route) => (
                          <TableRow key={route.route_id}>
                            <TableCell>
                              <div className="font-medium">{route.display_name}</div>
                            </TableCell>
                            <TableCell>
                              <div>{route.model_name}</div>
                              {route.supports_vision ? (
                                <Badge variant="outline">{t("models.visionBadge")}</Badge>
                              ) : null}
                            </TableCell>
                            <TableCell>
                              {connections.find(
                                (connection) => connection.connection_id === route.connection_id,
                              )?.display_name ?? t("models.connectionUnavailable")}
                            </TableCell>
                            <TableCell>
                              <StatusBadge
                                semantic={routeStatusSemantic(route.status)}
                                label={localizedStatusLabel(route.status, t)}
                              />
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-wrap gap-2">
                                {route.is_text_default ||
                                route.route_id === textDefaultRouteId ? (
                                  <StatusBadge
                                    semantic="success"
                                    label={t("models.textDefault")}
                                  />
                                ) : null}
                                {route.is_vision_default ||
                                route.route_id === visionDefaultRouteId ? (
                                  <StatusBadge
                                    semantic="success"
                                    label={t("models.visionDefault")}
                                  />
                                ) : null}
                                {!route.is_text_default &&
                                route.route_id !== textDefaultRouteId &&
                                !route.is_vision_default &&
                                route.route_id !== visionDefaultRouteId ? (
                                  <Badge variant="outline">
                                    {t("models.noDefaultRole")}
                                  </Badge>
                                ) : null}
                              </div>
                            </TableCell>
                            <TableCell>
                              <TechnicalDetails label={t("common.viewDetails")}>
                                <dl className="grid min-w-64 gap-2 rounded-md bg-muted/40 p-3 text-xs">
                                  <div>
                                    <dt className="text-muted-foreground">{t("admin.routeId")}</dt>
                                    <dd className="break-all font-mono">{route.route_id}</dd>
                                  </div>
                                  <div>
                                    <dt className="text-muted-foreground">{t("models.runtimePolicy")}</dt>
                                    <dd>{t("models.policyRevision", { revision: route.runtime_policy.revision })}</dd>
                                  </div>
                                  <div>
                                    <dt className="text-muted-foreground">{t("models.executionLimits")}</dt>
                                    <dd>{t("models.toolProviderLimits", {
                                      tools: route.runtime_policy.max_tool_executions,
                                      providers: route.runtime_policy.max_provider_invocations,
                                    })}</dd>
                                  </div>
                                  <div>
                                    <dt className="text-muted-foreground">{t("models.conversationTokenCap")}</dt>
                                    <dd>{route.runtime_policy.max_total_tokens_per_conversation.toLocaleString(locale)}</dd>
                                  </div>
                                </dl>
                              </TechnicalDetails>
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-wrap gap-2">
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => onEdit(route)}
                                >
                                  <Pencil data-icon="inline-start" />
                                  {t("admin.edit")}
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() =>
                                    void onTest(route)
                                  }
                                  disabled={pendingAction === `test-route-${route.route_id}`}
                                >
                                  <CheckCircle2 data-icon="inline-start" />
                                  {t("admin.testRoute")}
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() =>
                                    void onSetDefault(route, "text")
                                  }
                                  disabled={
                                    pendingAction ===
                                      `default-text-route-${route.route_id}` ||
                                    route.status !== "test_passed" ||
                                    route.is_text_default ||
                                    !route.enabled
                                  }
                                >
                                  <Cpu data-icon="inline-start" />
                                  {t("models.setTextDefault")}
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() =>
                                    void onSetDefault(route, "vision")
                                  }
                                  disabled={
                                    pendingAction ===
                                      `default-vision-route-${route.route_id}` ||
                                    route.status !== "test_passed" ||
                                    route.is_vision_default ||
                                    !route.enabled ||
                                    !route.supports_vision
                                  }
                                >
                                  <ImageIcon data-icon="inline-start" />
                                  {t("models.setVisionDefault")}
                                </Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </CardContent>
              </Card>
            )}
            </>
            )}
    </>
 );
}

function routeStatusSemantic(status: ModelRouteStatus["status"]) {
  if (status === "test_passed") return "success" as const;
  if (status === "configured") return "attention" as const;
  if (status === "test_failed") return "failure" as const;
  return "inactive" as const;
}
