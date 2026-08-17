import { History, MessageSquareText, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  AdminBreadcrumb,
  AdminResourceUnavailable,
} from "../../shared/admin-detail";
import {
  LoadErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  serverMessage,
} from "../../shared/product-ui";
import {
  adminAuditConversationRoute,
  adminAuditSectionRoute,
  type AppRoute,
} from "../../shared/routes";
import { useIsMobile } from "../../hooks/use-mobile";
import type {
  ConversationDetail,
  ConversationSummary,
  ConversationTurn,
  DeclaredEvidencePreview,
} from "../workspace/index";
import { EvidenceViewerDialog } from "../workspace/index";
import { AuditEventsView } from "./AuditEventsView";
import { AuditLandingView } from "./AuditLandingView";
import {
  formatDateTime,
  type DiscoveryPreview,
} from "./AuditPresentationUtils";
import { ConversationDirectoryView } from "./ConversationDirectoryView";
import { ConversationRuntimeView } from "./ConversationRuntimeView";
import { ConversationTranscriptView } from "./ConversationTranscriptView";
import type {
  AuditEvent,
  RuntimeTraceDetail,
} from "./types";

export function ConversationAuditPresentation({
  route,
  onNavigate,
  isLanding,
  isConversationDirectory,
  isEvents,
  isConversationDetail,
  isRuntimeDetail,
  resourceUnavailable,
  loading,
  loadError,
  events,
  eventsLoading,
  eventsLoadError,
  conversations,
  nextConversationCursor,
  conversationPageError,
  moreConversationsLoading,
  conversationHistoryRefreshing,
  selectedConversationId,
  selectedConversation,
  conversationLoading,
  conversationError,
  selectedRuntime,
  selectedRuntimeTurn,
  runtimeLoading,
  runtimeError,
  discoveryPreview,
  visibleDeclaredEvidence,
  visibleDeclaredEvidenceLoading,
  onLoadConversationHistory,
  onLoadEvents,
  onOpenConversation,
  onOpenRuntime,
  onOpenDeclaredEvidence,
  onDiscoveryPreviewChange,
  onCloseDeclaredEvidence,
}: {
  route: AppRoute;
  onNavigate: (route: AppRoute) => void;
  isLanding: boolean;
  isConversationDirectory: boolean;
  isEvents: boolean;
  isConversationDetail: boolean;
  isRuntimeDetail: boolean;
  resourceUnavailable: boolean;
  loading: boolean;
  loadError: string;
  events: AuditEvent[];
  eventsLoading: boolean;
  eventsLoadError: string;
  conversations: ConversationSummary[];
  nextConversationCursor: string | null;
  conversationPageError: string;
  moreConversationsLoading: boolean;
  conversationHistoryRefreshing: boolean;
  selectedConversationId: string | null;
  selectedConversation: ConversationDetail | null;
  conversationLoading: boolean;
  conversationError: string;
  selectedRuntime: RuntimeTraceDetail | null;
  selectedRuntimeTurn: ConversationTurn | null;
  runtimeLoading: boolean;
  runtimeError: string;
  discoveryPreview: DiscoveryPreview | null;
  visibleDeclaredEvidence: DeclaredEvidencePreview | null;
  visibleDeclaredEvidenceLoading: boolean;
  onLoadConversationHistory: (options?: {
    cursor?: string;
    append?: boolean;
    refresh?: boolean;
  }) => Promise<void>;
  onLoadEvents: () => Promise<void>;
  onOpenConversation: (conversationId: string) => Promise<void>;
  onOpenRuntime: (turn: ConversationTurn) => Promise<void>;
  onOpenDeclaredEvidence: (
    turn: ConversationTurn,
    protectedOpenRef: string,
  ) => Promise<void>;
  onDiscoveryPreviewChange: (preview: DiscoveryPreview | null) => void;
  onCloseDeclaredEvidence: () => void;
}) {
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
if (resourceUnavailable) {
    return (
      <AdminResourceUnavailable
        onBack={() => onNavigate(adminAuditSectionRoute("conversations"))}
      />
    );
  }

  if (isLanding) {
    return <AuditLandingView onNavigate={onNavigate} />;
  }

  if (loading && isConversationDirectory) {
    return (
      <section className="flex flex-col gap-5">
        <PageHeader title={t("audit.title")} />
        <LoadingState
          title={t("audit.loadingTitle")}
        />
      </section>
    );
  }

  if (loadError && isConversationDirectory) {
    return (
      <section className="flex flex-col gap-5">
        <PageHeader title={t("audit.title")} />
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={serverMessage(loadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => void onLoadConversationHistory()}
        />
      </section>
    );
  }

  return (
    <>
    <section className="flex flex-col gap-5">
      <PageHeader title={t("audit.title")} />
      {!isLanding && (
        <AdminBreadcrumb
          onNavigate={onNavigate}
          items={[
            { label: t("audit.title"), route: "/admin/audit" },
            ...(isEvents
              ? [{ label: t("audit.operationHistory") }]
              : [{ label: t("audit.conversationHistory"), route: adminAuditSectionRoute("conversations") }]),
            ...(isConversationDetail
              ? [
                  {
                    label: selectedConversation?.title ?? t("audit.conversationLoadingLabel"),
                    route: selectedConversation
                      ? adminAuditConversationRoute(
                          selectedConversation.conversation_id,
                          "transcript",
                        )
                      : undefined,
                  },
                  ...(isRuntimeDetail ? [{ label: t("audit.runtime") }] : []),
                ]
              : []),
          ]}
        />
      )}
        {!isEvents && (
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <CardTitle>
                    {isConversationDirectory
                      ? t("audit.conversationHistory")
                      : selectedConversation?.title ?? t("audit.conversationLoadingLabel")}
                  </CardTitle>
                  {selectedConversation && (
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <time dateTime={selectedConversation.updated_at}>
                        {t("audit.updatedAt", {
                          value: formatDateTime(
                            selectedConversation.updated_at,
                            i18n.resolvedLanguage ?? i18n.language,
                          ),
                        })}
                      </time>
                      <StatusBadge
                        semantic={selectedConversation.status === "active" ? "success" : "inactive"}
                        label={t(`audit.conversationStatus.${selectedConversation.status}`)}
                      />
                    </div>
                  )}
                </div>
                {isConversationDirectory ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => onNavigate(adminAuditSectionRoute("events"))}
                    >
                      <History aria-hidden="true" data-icon="inline-start" />
                      {t("audit.openOperationDirectory")}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={conversationHistoryRefreshing}
                      onClick={() => void onLoadConversationHistory({ refresh: true })}
                    >
                      <RefreshCw
                        aria-hidden="true"
                        data-icon="inline-start"
                        className={conversationHistoryRefreshing ? "animate-spin" : undefined}
                      />
                      {t("audit.refreshConversations")}
                    </Button>
                  </div>
                ) : isRuntimeDetail && selectedConversation ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      onNavigate(
                        adminAuditConversationRoute(
                          selectedConversation.conversation_id,
                          "transcript",
                        ),
                      )}
                  >
                    {t("audit.backToConversation")}
                  </Button>
                ) : null}
              </div>
            </CardHeader>
            <CardContent
              data-slot="audit-conversation-layout"
              className="grid min-w-0 gap-4"
            >
              {isConversationDirectory && (
                <ConversationDirectoryView
                  conversations={conversations}
                  isMobile={isMobile}
                  loading={loading}
                  nextConversationCursor={nextConversationCursor}
                  moreConversationsLoading={moreConversationsLoading}
                  conversationPageError={conversationPageError}
                  locale={i18n.resolvedLanguage ?? i18n.language}
                  onNavigate={onNavigate}
                  onLoadMore={(cursor) =>
                    onLoadConversationHistory({ cursor, append: true })}
                />
              )}
              {isConversationDetail && (
                isRuntimeDetail ? (
                <ConversationRuntimeView
                  conversationLoading={conversationLoading}
                  conversationError={conversationError}
                  selectedConversationId={selectedConversationId}
                  selectedRuntimeTurn={selectedRuntimeTurn}
                  runtimeLoading={runtimeLoading}
                  runtimeError={runtimeError}
                  selectedRuntime={selectedRuntime}
                  locale={i18n.resolvedLanguage ?? i18n.language}
                  onOpenConversation={onOpenConversation}
                  onOpenRuntime={onOpenRuntime}
                  onDiscoveryPreviewChange={onDiscoveryPreviewChange}
                />
                ) : (
                <ConversationTranscriptView
                  conversationLoading={conversationLoading}
                  conversationError={conversationError}
                  selectedConversationId={selectedConversationId}
                  selectedConversation={selectedConversation}
                  onNavigate={onNavigate}
                  onOpenConversation={onOpenConversation}
                  onOpenDeclaredEvidence={onOpenDeclaredEvidence}
                />
                )
              )}
            </CardContent>
          </Card>
        )}

        {isEvents && (
          <AuditEventsView
            events={events}
            loading={eventsLoading}
            loadError={eventsLoadError}
            isMobile={isMobile}
            locale={i18n.resolvedLanguage ?? i18n.language}
            onNavigate={onNavigate}
            onReload={onLoadEvents}
          />
        )}
    </section>
    <Dialog
      open={Boolean(discoveryPreview)}
      onOpenChange={(open) => {
        if (!open) onDiscoveryPreviewChange(null);
      }}
    >
      <DialogContent className="max-h-[85vh]">
        <DialogHeader>
          <DialogTitle>{t("audit.discoveryPreviewTitle")}</DialogTitle>
          <DialogDescription className="break-all">
            {t("audit.discoveryPreviewDescription", {
              document: discoveryPreview?.document_display_name ?? t("audit.notReported"),
              locator: discoveryPreview?.locator_label ?? t("audit.notReported"),
            })}
          </DialogDescription>
        </DialogHeader>
        <div
          data-slot="audit-discovery-preview"
          className="max-h-[65vh] overflow-y-auto whitespace-pre-wrap break-words rounded-md border bg-muted/30 p-4 text-sm"
        >
          {discoveryPreview?.preview}
        </div>
      </DialogContent>
    </Dialog>
    <EvidenceViewerDialog
      evidence={visibleDeclaredEvidence}
      loading={visibleDeclaredEvidenceLoading}
      onClose={() => {
        onCloseDeclaredEvidence();
      }}
    />
    </>
  );
}
