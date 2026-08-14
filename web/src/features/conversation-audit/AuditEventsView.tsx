import { History, MessageSquareText, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import {
  Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle,
} from "../../components/ui/empty";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "../../components/ui/table";
import { LoadErrorState, LoadingState, serverMessage } from "../../shared/product-ui";
import { adminAuditSectionRoute, type AppRoute } from "../../shared/routes";
import type { AuditEvent } from "./types";
import { formatDateTime } from "./AuditPresentationUtils";

export function AuditEventsView({
  events,
  loading,
  loadError,
  isMobile,
  locale,
  onNavigate,
  onReload,
}: {
  events: AuditEvent[];
  loading: boolean;
  loadError: string;
  isMobile: boolean;
  locale: string;
  onNavigate: (route: AppRoute) => void;
  onReload: () => Promise<void>;
}) {
  const { t } = useTranslation();
  return (
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <CardTitle>{t("audit.operationHistory")}</CardTitle>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => onNavigate(adminAuditSectionRoute("conversations"))}
                  >
                    <MessageSquareText aria-hidden="true" data-icon="inline-start" />
                    {t("audit.openConversationDirectory")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={loading}
                    onClick={() => void onReload()}
                  >
                    <RefreshCw aria-hidden="true" data-icon="inline-start" />
                    {t("ops.refresh")}
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {loading ? (
                <LoadingState
                  title={t("audit.loading")}
                />
              ) : loadError ? (
                <LoadErrorState
                  title={t("admin.listLoadFailed")}
                  description={loadError}
                  retryLabel={t("admin.retry")}
                  onRetry={() => void onReload()}
                />
              ) : events.length === 0 ? (
                <Empty className="border">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <History />
                    </EmptyMedia>
                    <EmptyTitle>{t("audit.emptyTitle")}</EmptyTitle>
                    <EmptyDescription>{t("audit.emptyDescription")}</EmptyDescription>
                  </EmptyHeader>
                </Empty>
              ) : isMobile ? (
                <div className="grid gap-3">
                  {events.map((event) => (
                    <Card key={event.event_id}>
                      <CardContent className="grid gap-2 pt-4 text-sm">
                        <div className="font-medium">{event.event_type}</div>
                        <time
                          className="text-muted-foreground"
                          dateTime={event.created_at}
                        >
                          {formatDateTime(
                            event.created_at,
                            locale,
                          )}
                        </time>
                        <div>{serverMessage(event, t)}</div>
                        <div className="break-all text-xs text-muted-foreground">
                          {event.target_ref ?? "-"}
                        </div>
                        <div className="break-all font-mono text-xs">
                          {metadataFingerprint(event)}
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("audit.eventType")}</TableHead>
                      <TableHead>{t("audit.time")}</TableHead>
                      <TableHead>{t("audit.message")}</TableHead>
                      <TableHead>{t("audit.target")}</TableHead>
                      <TableHead>{t("agents.fingerprint")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {events.map((event) => (
                      <TableRow key={event.event_id}>
                        <TableCell>{event.event_type}</TableCell>
                        <TableCell>
                          <time dateTime={event.created_at}>
                            {formatDateTime(
                              event.created_at,
                              locale,
                            )}
                          </time>
                        </TableCell>
                        <TableCell>{serverMessage(event, t)}</TableCell>
                        <TableCell>{event.target_ref ?? "-"}</TableCell>
                        <TableCell>{metadataFingerprint(event)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
  );
}

function metadataFingerprint(event: AuditEvent) {
  const value = event.metadata.token_fingerprint;
  return typeof value === "string" ? value : "-";
}
