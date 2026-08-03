import { History, MessageSquareText, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import { Badge } from "../../components/ui/badge";
import { Bubble, BubbleContent } from "../../components/ui/bubble";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "../../components/ui/empty";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Message,
  MessageContent,
  MessageHeader,
} from "../../components/ui/message";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import {
  AdminBreadcrumb,
  AdminResourceUnavailable,
} from "../../shared/admin-detail";
import {
  LoadErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  TechnicalDetails,
  conversationTurnStatusPresentation,
  serverMessage,
} from "../../shared/product-ui";
import {
  adminAuditConversationRoute,
  adminAuditSectionRoute,
  matchAppRoute,
  type AppRoute,
} from "../../shared/routes";
import { ApiError } from "../../shared/user-messages";
import { useIsMobile } from "../../hooks/use-mobile";
import type {
  ConversationDetail,
  ConversationSummary,
  ConversationTurn,
  DeclaredEvidencePreview,
} from "../workspace/index";
import { ClaimedEvidenceTrace, EvidenceViewerDialog } from "../workspace/index";
import { conversationAuditApi } from "./api";
import type {
  AuditEvent,
  DiscoveryCandidateTrace,
  ReasoningTrace,
  RuntimeTraceDetail,
} from "./types";

type DiscoveryPreview = Pick<
  DiscoveryCandidateTrace,
  "document_display_name" | "locator_label" | "preview"
>;

function assistantAttemptPosition(
  turn: ConversationTurn,
  turns: ConversationTurn[],
) {
  if (turn.role !== "assistant" || !turn.source_turn_id) return null;

  const attempts = turns
    .map((candidate, index) => ({ candidate, index }))
    .filter(
      ({ candidate }) =>
        candidate.role === "assistant" &&
        candidate.source_turn_id === turn.source_turn_id,
    )
    .sort((left, right) => {
      const leftTime = Date.parse(left.candidate.created_at);
      const rightTime = Date.parse(right.candidate.created_at);
      if (Number.isNaN(leftTime) && Number.isNaN(rightTime)) {
        return left.index - right.index;
      }
      if (Number.isNaN(leftTime)) return 1;
      if (Number.isNaN(rightTime)) return -1;
      return leftTime === rightTime ? left.index - right.index : leftTime - rightTime;
    });
  const position = attempts.findIndex(({ candidate }) => candidate === turn);
  return position < 0
    ? null
    : { ordinal: position + 1, total: attempts.length };
}

export function ConversationAuditFeature({
  route,
  onNavigate,
}: {
  route: AppRoute;
  onNavigate: (route: AppRoute) => void;
}) {
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  const routeMatch = matchAppRoute(route);
  const isLanding = route === "/admin/audit";
  const isConversationDirectory =
    isLanding ||
    (routeMatch.kind === "admin-audit-section" &&
      routeMatch.section === "conversations");
  const isEvents =
    routeMatch.kind === "admin-audit-section" &&
    routeMatch.section === "events";
  const isConversationDetail = routeMatch.kind === "admin-audit-conversation";
  const isRuntimeDetail =
    routeMatch.kind === "admin-audit-conversation" &&
    routeMatch.section === "runtime";
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsLoadError, setEventsLoadError] = useState("");
  const [eventsInitialized, setEventsInitialized] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [nextConversationCursor, setNextConversationCursor] = useState<string | null>(null);
  const [conversationPageError, setConversationPageError] = useState("");
  const [moreConversationsLoading, setMoreConversationsLoading] = useState(false);
  const [conversationHistoryRefreshing, setConversationHistoryRefreshing] = useState(false);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [loadedConversation, setSelectedConversation] =
    useState<ConversationDetail | null>(null);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState("");
  const [loadedRuntime, setSelectedRuntime] =
    useState<RuntimeTraceDetail | null>(null);
  const [loadedRuntimeTurn, setSelectedRuntimeTurn] =
    useState<ConversationTurn | null>(null);
  const [runtimeLoading, setRuntimeLoading] = useState(false);
  const [runtimeError, setRuntimeError] = useState("");
  const [discoveryPreview, setDiscoveryPreview] =
    useState<DiscoveryPreview | null>(null);
  const [unavailableRoute, setUnavailableRoute] = useState<AppRoute | null>(null);
  const [declaredEvidence, setDeclaredEvidence] =
    useState<DeclaredEvidencePreview | null>(null);
  const [declaredEvidenceRoute, setDeclaredEvidenceRoute] =
    useState<AppRoute | null>(null);
  const [declaredEvidenceLoading, setDeclaredEvidenceLoading] = useState(false);
  const conversationRequestGeneration = useRef(0);
  const runtimeRequestGeneration = useRef(0);
  const initialConversationLoadStarted = useRef(false);
  const initialEventsLoadStarted = useRef(false);
  const loadedConversationRoute = useRef("");
  const loadedRuntimeRoute = useRef("");
  const activeConversationId = useRef<string | null>(null);
  const activeRuntimeKey = useRef<string | null>(null);
  const activeRoute = useRef(route);
  activeConversationId.current =
    routeMatch.kind === "admin-audit-conversation"
      ? routeMatch.conversationId
      : null;
  activeRuntimeKey.current =
    routeMatch.kind === "admin-audit-conversation" &&
    routeMatch.section === "runtime"
      ? `${routeMatch.conversationId}:${routeMatch.turnId}`
      : null;
  activeRoute.current = route;
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const selectedConversation =
    routeMatch.kind === "admin-audit-conversation" &&
    loadedConversation?.conversation_id === routeMatch.conversationId
      ? loadedConversation
      : null;
  const selectedRuntimeTurn =
    routeMatch.kind === "admin-audit-conversation" &&
    routeMatch.section === "runtime" &&
    loadedRuntimeTurn?.conversation_id === routeMatch.conversationId &&
    loadedRuntimeTurn.turn_id === routeMatch.turnId
      ? loadedRuntimeTurn
      : null;
  const selectedRuntime = selectedRuntimeTurn ? loadedRuntime : null;
  const resourceUnavailable = unavailableRoute === route;
  const visibleDeclaredEvidence =
    declaredEvidenceRoute === route ? declaredEvidence : null;
  const visibleDeclaredEvidenceLoading =
    declaredEvidenceRoute === route && declaredEvidenceLoading;

  async function loadConversationHistory({
    cursor,
    append = false,
    refresh = false,
  }: {
    cursor?: string;
    append?: boolean;
    refresh?: boolean;
  } = {}) {
    if (append) {
      setMoreConversationsLoading(true);
      setConversationPageError("");
    } else if (refresh) {
      setConversationHistoryRefreshing(true);
      setConversationPageError("");
    } else {
      setLoading(true);
      setLoadError("");
    }
    try {
      const conversationResult = await conversationAuditApi.listAdminConversations(cursor);
      setConversations((current) => {
        if (!append) return conversationResult.conversations;
        const known = new Set(current.map((item) => item.conversation_id));
        return [
          ...current,
          ...conversationResult.conversations.filter(
            (item) => !known.has(item.conversation_id),
          ),
        ];
      });
      setNextConversationCursor(conversationResult.next_cursor ?? null);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : t("admin.listLoadFailed");
      if (append || refresh) setConversationPageError(message);
      else setLoadError(message);
    } finally {
      if (append) setMoreConversationsLoading(false);
      else if (refresh) setConversationHistoryRefreshing(false);
      else setLoading(false);
    }
  }

  async function loadEvents() {
    setEventsLoading(true);
    setEventsLoadError("");
    try {
      const result = await conversationAuditApi.listAuditEvents();
      setEvents(result.events);
    } catch (caught) {
      setEventsLoadError(serverMessage(caught, t));
    } finally {
      setEventsInitialized(true);
      setEventsLoading(false);
    }
  }

  async function openConversation(conversationId: string) {
    const requestGeneration = ++conversationRequestGeneration.current;
    runtimeRequestGeneration.current += 1;
    loadedRuntimeRoute.current = "";
    setSelectedConversationId(conversationId);
    setSelectedConversation(null);
    setConversationLoading(true);
    setConversationError("");
    setUnavailableRoute(null);
    setSelectedRuntime(null);
    setSelectedRuntimeTurn(null);
    setRuntimeLoading(false);
    setRuntimeError("");
    try {
      const detail = await conversationAuditApi.getAdminConversation(conversationId);
      if (
        conversationRequestGeneration.current !== requestGeneration ||
        activeConversationId.current !== conversationId
      ) return;
      setSelectedConversation(detail);
    } catch (caught) {
      if (
        conversationRequestGeneration.current !== requestGeneration ||
        activeConversationId.current !== conversationId
      ) return;
      if (caught instanceof ApiError && (caught.status === 403 || caught.status === 404)) {
        setUnavailableRoute(activeRoute.current);
        return;
      }
      setConversationError(serverMessage(caught, t));
    } finally {
      if (
        conversationRequestGeneration.current === requestGeneration &&
        activeConversationId.current === conversationId
      ) {
        setConversationLoading(false);
      }
    }
  }

  async function openRuntime(turn: ConversationTurn) {
    const requestGeneration = ++runtimeRequestGeneration.current;
    const runtimeKey = `${turn.conversation_id}:${turn.turn_id}`;
    setSelectedRuntimeTurn(turn);
    setSelectedRuntime(null);
    setRuntimeLoading(true);
    setRuntimeError("");
    try {
      const detail = await conversationAuditApi.getAdminConversationRuntime(
        turn.conversation_id,
        turn.turn_id,
      );
      if (
        runtimeRequestGeneration.current !== requestGeneration ||
        activeRuntimeKey.current !== runtimeKey
      ) return;
      setSelectedRuntime(detail);
    } catch (caught) {
      if (
        runtimeRequestGeneration.current !== requestGeneration ||
        activeRuntimeKey.current !== runtimeKey
      ) return;
      if (caught instanceof ApiError && (caught.status === 403 || caught.status === 404)) {
        setUnavailableRoute(activeRoute.current);
        return;
      }
      setRuntimeError(serverMessage(caught, t));
    } finally {
      if (
        runtimeRequestGeneration.current === requestGeneration &&
        activeRuntimeKey.current === runtimeKey
      ) {
        setRuntimeLoading(false);
      }
    }
  }

  async function openDeclaredEvidence(
    turn: ConversationTurn,
    protectedOpenRef: string,
  ) {
    const requestRoute = activeRoute.current;
    setDeclaredEvidence(null);
    setDeclaredEvidenceRoute(requestRoute);
    setDeclaredEvidenceLoading(true);
    try {
      const evidence = await conversationAuditApi.readAdminDeclaredEvidence(
        turn.conversation_id,
        turn.turn_id,
        protectedOpenRef,
      );
      if (activeRoute.current !== requestRoute) return;
      setDeclaredEvidence({
        kind: "excerpt",
        evidence,
      });
    } catch (caught) {
      if (activeRoute.current !== requestRoute) return;
      setDeclaredEvidence(null);
      toast.error(serverMessage(caught, t));
    } finally {
      if (activeRoute.current === requestRoute) {
        setDeclaredEvidenceLoading(false);
      }
    }
  }

  useEffect(() => {
    if (!isConversationDirectory || initialConversationLoadStarted.current) return;
    initialConversationLoadStarted.current = true;
    void loadConversationHistory();
  }, [isConversationDirectory]);

  useEffect(() => {
    if (
      isEvents &&
      !eventsInitialized &&
      !eventsLoading &&
      !initialEventsLoadStarted.current
    ) {
      initialEventsLoadStarted.current = true;
      void loadEvents();
    }
  }, [isEvents, eventsInitialized, eventsLoading]);

  useEffect(() => {
    setUnavailableRoute(null);
    setDeclaredEvidence(null);
    setDeclaredEvidenceRoute(null);
    setDeclaredEvidenceLoading(false);
    setDiscoveryPreview(null);
    if (routeMatch.kind !== "admin-audit-conversation") {
      conversationRequestGeneration.current += 1;
      loadedConversationRoute.current = "";
      setSelectedConversationId(null);
      setSelectedConversation(null);
      setConversationLoading(false);
      setConversationError("");
    }
    if (
      routeMatch.kind !== "admin-audit-conversation" ||
      routeMatch.section !== "runtime"
    ) {
      runtimeRequestGeneration.current += 1;
      loadedRuntimeRoute.current = "";
      setSelectedRuntime(null);
      setSelectedRuntimeTurn(null);
      setRuntimeLoading(false);
      setRuntimeError("");
    }
  }, [route]);

  useEffect(() => {
    if (routeMatch.kind !== "admin-audit-conversation") return;
    if (loadedConversationRoute.current === routeMatch.conversationId) return;
    loadedConversationRoute.current = routeMatch.conversationId;
    void openConversation(routeMatch.conversationId);
  }, [route]);

  useEffect(() => {
    if (
      routeMatch.kind !== "admin-audit-conversation" ||
      routeMatch.section !== "runtime" ||
      !selectedConversation ||
      selectedConversation.conversation_id !== routeMatch.conversationId
    ) return;
    const turn = selectedConversation.turns.find(
      (candidate) =>
        candidate.turn_id === routeMatch.turnId &&
        candidate.role === "assistant" &&
        Boolean(candidate.runtime_trace_id),
    );
    if (!turn) {
      setUnavailableRoute(route);
      return;
    }
    const runtimeKey = `${routeMatch.conversationId}:${routeMatch.turnId}`;
    if (loadedRuntimeRoute.current === runtimeKey) return;
    loadedRuntimeRoute.current = runtimeKey;
    void openRuntime(turn);
  }, [route, selectedConversation]);

  if (resourceUnavailable) {
    return (
      <AdminResourceUnavailable
        onBack={() => onNavigate(adminAuditSectionRoute("conversations"))}
      />
    );
  }

  if (loading && isConversationDirectory) {
    return (
      <section className="flex flex-col gap-5">
        <PageHeader title={t("audit.title")} description={t("audit.description")} />
        <LoadingState
          title={t("audit.loadingTitle")}
        />
      </section>
    );
  }

  if (loadError && isConversationDirectory) {
    return (
      <section className="flex flex-col gap-5">
        <PageHeader title={t("audit.title")} description={t("audit.description")} />
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={serverMessage(loadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => void loadConversationHistory()}
        />
      </section>
    );
  }

  return (
    <>
    <section className="flex flex-col gap-5">
      <PageHeader title={t("audit.title")} description={t("audit.description")} />
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
                      onClick={() => void loadConversationHistory({ refresh: true })}
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
              <nav
                aria-label={t("audit.conversationIndex")}
                className="flex min-w-0 flex-col gap-2"
              >
                {conversations.length === 0 && !loading ? (
                  <Empty className="border">
                    <EmptyHeader>
                      <EmptyMedia variant="icon">
                        <MessageSquareText />
                      </EmptyMedia>
                      <EmptyTitle>{t("audit.noConversations")}</EmptyTitle>
                      <EmptyDescription>{t("audit.selectConversation")}</EmptyDescription>
                    </EmptyHeader>
                  </Empty>
                ) : (
                  <>
                  {isMobile ? (
                    <div className="grid gap-3">
                      {conversations.map((conversation) => (
                        <Card key={conversation.conversation_id}>
                          <CardContent className="grid gap-2 pt-4">
                            <div className="font-medium">{conversation.title}</div>
                            <div className="break-all text-xs text-muted-foreground">
                              {conversation.owner_actor_id}
                            </div>
                            <Button
                              variant="outline"
                              className="justify-start"
                              aria-label={`${t("audit.openConversation")} ${conversation.title}`}
                              onClick={() =>
                                onNavigate(
                                  adminAuditConversationRoute(
                                    conversation.conversation_id,
                                    "transcript",
                                  ),
                                )}
                            >
                              {t("audit.openConversation")}
                            </Button>
                          </CardContent>
                        </Card>
                      ))}
                    </div>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>{t("audit.conversation")}</TableHead>
                          <TableHead>{t("audit.owner")}</TableHead>
                          <TableHead>{t("audit.updatedAtLabel")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {conversations.map((conversation) => (
                          <TableRow key={conversation.conversation_id}>
                            <TableCell>
                              <Button
                                variant="ghost"
                                className="h-auto justify-start px-0 text-left"
                                onClick={() =>
                                  onNavigate(
                                    adminAuditConversationRoute(
                                      conversation.conversation_id,
                                      "transcript",
                                    ),
                                  )}
                              >
                                {conversation.title}
                              </Button>
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {conversation.owner_actor_id}
                            </TableCell>
                            <TableCell>
                              {formatDateTime(
                                conversation.updated_at,
                                i18n.resolvedLanguage ?? i18n.language,
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                  {nextConversationCursor && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={moreConversationsLoading}
                      onClick={() => void loadConversationHistory({
                        cursor: nextConversationCursor,
                        append: true,
                      })}
                    >
                      {moreConversationsLoading
                        ? t("audit.loadingMoreConversations")
                        : t("audit.loadMoreConversations")}
                    </Button>
                  )}
                  {conversationPageError && (
                    <p role="alert" className="text-sm text-destructive">
                      {serverMessage(conversationPageError, t)}
                    </p>
                  )}
                  </>
                )}
              </nav>
              )}
              {isConversationDetail && (
                !isRuntimeDetail ? (
                <div className="min-w-0">
                  {conversationLoading ? (
                    <LoadingState
                      title={t("audit.transcriptLoadingTitle")}
                    />
                  ) : conversationError && selectedConversationId ? (
                    <LoadErrorState
                      title={t("audit.transcriptLoadFailed")}
                      description={conversationError}
                      retryLabel={t("admin.retry")}
                      onRetry={() => void openConversation(selectedConversationId)}
                    />
                  ) : !selectedConversation ? (
                    <p className="text-sm text-muted-foreground">
                      {t("audit.selectConversation")}
                    </p>
                  ) : selectedConversation.turns.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {t("audit.emptyTranscript")}
                    </p>
                  ) : (
                    <div data-slot="audit-transcript" className="grid min-w-0 gap-4 p-1">
                      {selectedConversation.turns.map((turn) => {
                        const align = turn.role === "user" ? "end" : "start";
                        const attemptPosition = assistantAttemptPosition(
                          turn,
                          selectedConversation.turns,
                        );
                        const hasCompleteAttemptLineage = Boolean(
                          turn.source_turn_id &&
                          turn.execution_id &&
                          attemptPosition,
                        );
                        return (
                          <div key={turn.turn_id} className="min-w-0">
                            <Message align={align}>
                              <MessageContent>
                                <MessageHeader
                                  className={
                                    turn.role === "user"
                                      ? "justify-end"
                                      : "justify-between gap-2"
                                  }
                                >
                                  <span>
                                    {t(turn.role === "user" ? "audit.userRole" : "audit.assistantRole")}
                                  </span>
                                  {turn.role === "assistant" && turn.runtime_trace_id && (
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      onClick={() =>
                                        onNavigate(
                                          adminAuditConversationRoute(
                                            turn.conversation_id,
                                            "runtime",
                                            turn.turn_id,
                                          ),
                                        )}
                                    >
                                      {t("audit.viewRuntimeTrace")}
                                    </Button>
                                  )}
                                  {turn.role === "assistant" && !turn.runtime_trace_id && (
                                    <StatusBadge
                                      semantic="inactive"
                                      label={t("audit.runtimeUnavailable")}
                                    />
                                  )}
                                </MessageHeader>
                                <Bubble
                                  align={align}
                                  variant={turn.role === "user" ? "default" : "outline"}
                                >
                                  <BubbleContent className="flex flex-col gap-3">
                                    {turn.role === "assistant" && (
                                      <TechnicalDetails label={t("common.technicalDetails")}>
                                      <div
                                        data-slot="assistant-attempt-lineage"
                                        data-testid="assistant-attempt-lineage"
                                        className="rounded-md border bg-muted/50 p-3"
                                      >
                                        {!hasCompleteAttemptLineage && (
                                          <div className="mb-3">
                                            <StatusBadge
                                              semantic="attention"
                                              label={t("audit.attemptLineageIncomplete")}
                                            />
                                          </div>
                                        )}
                                        <dl className="grid gap-3 sm:grid-cols-2">
                                          <div>
                                            <dt className="text-xs font-medium text-muted-foreground">
                                              {t("audit.attemptOrder")}
                                            </dt>
                                            <dd className="mt-1 font-medium">
                                              {attemptPosition
                                                ? t("audit.attemptOrdinal", {
                                                    ordinal: attemptPosition.ordinal,
                                                    total: attemptPosition.total,
                                                  })
                                                : t("audit.notReported")}
                                            </dd>
                                          </div>
                                          <div>
                                            <dt className="text-xs font-medium text-muted-foreground">
                                              {t("audit.executionStatus")}
                                            </dt>
                                            <dd className="mt-1">
                                              <StatusBadge
                                                semantic={conversationTurnStatusPresentation(
                                                  turn,
                                                  t,
                                                ).semantic}
                                                label={t(
                                                  `statusValues.${turn.execution_status}`,
                                                )}
                                              />
                                            </dd>
                                          </div>
                                          <div>
                                            <dt className="text-xs font-medium text-muted-foreground">
                                              {t("audit.requestReference")}
                                            </dt>
                                            <dd className="mt-1 break-all font-mono text-xs">
                                              {turn.source_turn_id || t("audit.notReported")}
                                            </dd>
                                          </div>
                                          <div>
                                            <dt className="text-xs font-medium text-muted-foreground">
                                              {t("audit.attemptReference")}
                                            </dt>
                                            <dd className="mt-1 break-all font-mono text-xs">
                                              {turn.execution_id || t("audit.notReported")}
                                            </dd>
                                          </div>
                                        </dl>
                                      </div>
                                      </TechnicalDetails>
                                    )}
                                    <span>
                                      {turn.input_text ?? turn.answer_text ?? serverMessage(turn.user_reason, t)}
                                    </span>
                                    {turn.role === "assistant" && turn.evidence_review_status && (
                                      <div className="flex flex-wrap items-center gap-2">
                                        <StatusBadge
                                          semantic={
                                            turn.evidence_review_status === "evidence_aligned"
                                              ? "success"
                                              : "attention"
                                          }
                                          label={t(
                                            turn.evidence_review_status === "evidence_aligned"
                                              ? "workspace.evidenceAligned"
                                              : "workspace.needsHumanReview",
                                          )}
                                        />
                                      </div>
                                    )}
                                    {turn.role === "assistant" && turn.assessment_state && (
                                      <TechnicalDetails label={t("audit.sourceCheckDetails")}>
                                      <dl
                                        data-testid="evidence-review-assessment"
                                        className="grid gap-2 rounded-md border bg-muted/30 p-3 text-xs sm:grid-cols-2"
                                      >
                                        <AuditTraceValue
                                          label={t("audit.assessmentState")}
                                          value={turn.assessment_state}
                                        />
                                        <AuditTraceValue
                                          label={t("audit.assessmentReason")}
                                          value={turn.assessment_reason_code}
                                        />
                                        <AuditTraceValue
                                          label={t("audit.evidenceReviewReasons")}
                                          value={turn.evidence_review_reason_codes.join(", ") || null}
                                        />
                                        <AuditTraceValue
                                          label={t("audit.assessmentInputDigest")}
                                          value={turn.assessment_input_digest}
                                        />
                                        <AuditTraceValue
                                          label={t("audit.assessmentOutputDigest")}
                                          value={turn.assessment_output_digest}
                                        />
                                      </dl>
                                      </TechnicalDetails>
                                    )}
                                    {turn.citations.length > 0 && (
                                      <div className="grid gap-2">
                                        {turn.citations.map((citation) => (
                                          <div
                                            key={citation.citation_id}
                                            className="rounded-md bg-muted p-2 text-sm"
                                          >
                                            <div className="font-medium">
                                              {citation.document_title}
                                            </div>
                                            <div className="text-muted-foreground">
                                              {citation.locator_label}
                                            </div>
                                            <div>{citation.snippet}</div>
                                          </div>
                                        ))}
                                      </div>
                                    )}
                                    {turn.role === "assistant" && (
                                      <TechnicalDetails label={t("audit.evidenceDetails")}>
                                      <ClaimedEvidenceTrace
                                        items={turn.model_claimed_evidence}
                                        showEmpty={turn.execution_status === "completed"}
                                        onOpen={(protectedOpenRef) =>
                                          void openDeclaredEvidence(turn, protectedOpenRef)}
                                      />
                                      </TechnicalDetails>
                                    )}
                                  </BubbleContent>
                                </Bubble>
                              </MessageContent>
                            </Message>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
                ) : (
                <div className="min-w-0 max-h-[min(42rem,70vh)] overflow-y-auto">
                  {selectedRuntimeTurn && (
                    <div className="mb-3 flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground">
                      <time dateTime={selectedRuntimeTurn.created_at}>
                        {t("audit.runtimeTurn", {
                          value: formatDateTime(
                            selectedRuntimeTurn.created_at,
                            i18n.resolvedLanguage ?? i18n.language,
                          ),
                        })}
                      </time>
                      <StatusBadge
                        {...conversationTurnStatusPresentation(selectedRuntimeTurn, t)}
                      />
                    </div>
                  )}
                  <div className="flex flex-col gap-4">
                    {conversationLoading ? (
                      <LoadingState
                        title={t("audit.transcriptLoadingTitle")}
                      />
                    ) : conversationError && selectedConversationId ? (
                      <LoadErrorState
                        title={t("audit.transcriptLoadFailed")}
                        description={conversationError}
                        retryLabel={t("admin.retry")}
                        onRetry={() => void openConversation(selectedConversationId)}
                      />
                    ) : runtimeLoading ? (
                      <LoadingState
                        title={t("audit.runtimeLoadingTitle")}
                      />
                    ) : runtimeError && selectedRuntimeTurn ? (
                      <LoadErrorState
                        title={t("audit.runtimeLoadFailed")}
                        description={runtimeError}
                        retryLabel={t("admin.retry")}
                        onRetry={() => void openRuntime(selectedRuntimeTurn)}
                      />
                    ) : !selectedRuntime ? (
                      <p className="text-sm text-muted-foreground">
                        {t("audit.selectRuntime")}
                      </p>
                    ) : (
                      <>
                        <div>
                          <div className="font-medium">{t("audit.traceIdentity")}</div>
                          <div className="mt-2 grid gap-3 rounded-md border p-3 sm:grid-cols-2">
                            <AuditField label={t("audit.traceId")} value={selectedRuntime.execution_id} />
                            <AuditField label={t("audit.finalValidationStatus")} value={selectedRuntime.state} />
                            <AuditField label={t("audit.attemptReference")} value={String(selectedRuntime.version)} />
                            <AuditField label={t("audit.reasoningMode")} value={selectedRuntime.reasoning_mode} />
                            <AuditField label={t("audit.errorCode")} value={selectedRuntime.failure_code ?? "—"} />
                            <AuditField
                              label={t("audit.answerGuidanceRevision")}
                              value={String(selectedRuntime.applied_guidance_revision)}
                            />
                            <AuditField
                              label={t("audit.answerGuidanceDigest")}
                              value={selectedRuntime.applied_guidance_digest ?? "—"}
                            />
                            <AuditField
                              label={t("audit.traceCreatedAt")}
                              value={formatDateTime(
                                selectedRuntime.created_at,
                                i18n.resolvedLanguage ?? i18n.language,
                              )}
                            />
                            <AuditField
                              label={t("audit.updatedAtLabel")}
                              value={formatDateTime(
                                selectedRuntime.updated_at,
                                i18n.resolvedLanguage ?? i18n.language,
                              )}
                            />
                          </div>
                        </div>
                        {selectedRuntime.reasoning_mode === "deep" ? (
                          selectedRuntime.reasoning_trace ? (
                            <ReasoningTracePanel trace={selectedRuntime.reasoning_trace} />
                          ) : (
                            <div>
                              <div className="font-medium">{t("audit.reasoningTrace")}</div>
                              <p className="mt-1 text-sm text-muted-foreground">
                                {t("audit.reasoningTraceUnavailable")}
                              </p>
                            </div>
                          )
                        ) : null}
                        <div>
                          <div className="font-medium">{t("audit.runtimeBudget")}</div>
                          <Table className="mt-2">
                            <TableHeader>
                              <TableRow>
                                <TableHead>{t("audit.providerCalls")}</TableHead>
                                <TableHead>{t("audit.toolCalls")}</TableHead>
                                <TableHead>{t("audit.searchRounds")}</TableHead>
                                <TableHead>{t("audit.uniqueEvidence")}</TableHead>
                                <TableHead>{t("audit.catalogPages")}</TableHead>
                                <TableHead>{t("audit.documentCandidates")}</TableHead>
                                <TableHead>{t("audit.contextTokens")}</TableHead>
                                <TableHead>{t("audit.toolTokens")}</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              <TableRow>
                                <TableCell>{selectedRuntime.budget.provider_invocations}</TableCell>
                                <TableCell>{selectedRuntime.budget.tool_invocations}</TableCell>
                                <TableCell>{selectedRuntime.budget.search_rounds}</TableCell>
                                <TableCell>{selectedRuntime.budget.unique_evidence}</TableCell>
                                <TableCell>{selectedRuntime.budget.catalog_pages}</TableCell>
                                <TableCell>{selectedRuntime.budget.document_candidates}</TableCell>
                                <TableCell>{selectedRuntime.budget.context_tokens}</TableCell>
                                <TableCell>{selectedRuntime.budget.tool_tokens}</TableCell>
                              </TableRow>
                            </TableBody>
                          </Table>
                        </div>
                        <div>
                          <div className="font-medium">{t("audit.documentDiscoveryTrace")}</div>
                          <p className="mt-1 text-sm text-muted-foreground">
                            {t("audit.documentDiscoveryTraceDescription")}
                          </p>
                          {selectedRuntime.document_discovery.length === 0 ? (
                            <p className="mt-2 text-sm text-muted-foreground">
                              {t("audit.noDocumentDiscovery")}
                            </p>
                          ) : (
                            <div className="mt-2 grid gap-3">
                              {selectedRuntime.document_discovery.map((trace) => (
                                <div key={trace.result_ref} className="rounded-md border p-3">
                                  <div className="grid gap-2 sm:grid-cols-2">
                                    <AuditField label={t("audit.discoveryQuery")} value={trace.query_text} />
                                    <AuditField label={t("audit.invocationId")} value={trace.invocation_id} />
                                    <AuditField label={t("audit.resultRef")} value={trace.result_ref} />
                                    <AuditField
                                      label={t("audit.discoveryChannels")}
                                      value={trace.channels
                                        .map((channel) => `${channel.channel}: ${channel.status}`)
                                        .join(", ")}
                                    />
                                    <AuditField
                                      label={t("audit.discoveryState")}
                                      value={
                                        trace.failure_code
                                          ? trace.failure_code
                                          : trace.degraded
                                            ? t("audit.discoveryDegraded")
                                            : t("audit.discoveryComplete")
                                      }
                                    />
                                    <AuditField
                                      label={t("audit.requestedLimit")}
                                      value={String(trace.requested_limit)}
                                    />
                                  </div>
                                  <Table className="mt-3 min-w-[56rem] table-fixed">
                                    <TableHeader>
                                      <TableRow>
                                        <TableHead className="w-20 whitespace-normal">
                                          {t("audit.candidateOrder")}
                                        </TableHead>
                                        <TableHead className="w-[18%] whitespace-normal">
                                          {t("audit.documentHandle")}
                                        </TableHead>
                                        <TableHead className="w-[30%] whitespace-normal">
                                          {t("audit.documentAndLocator")}
                                        </TableHead>
                                        <TableHead className="w-[34%] whitespace-normal">
                                          {t("audit.lineageReferences")}
                                        </TableHead>
                                        <TableHead className="w-32 whitespace-normal">
                                          {t("audit.accessState")}
                                        </TableHead>
                                      </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                      {trace.candidates.map((candidate) => (
                                        <TableRow key={`${trace.result_ref}:${candidate.position}`}>
                                          <TableCell>{candidate.position}</TableCell>
                                          <TableCell className="break-all whitespace-normal font-mono text-xs">
                                            {candidate.document_handle}
                                          </TableCell>
                                          <TableCell className="whitespace-normal">
                                            {candidate.resolution_status === "resolved" ? (
                                              <div className="grid gap-1">
                                                <span className="break-words font-medium">
                                                  {candidate.document_display_name}
                                                </span>
                                                <span className="text-xs text-muted-foreground">
                                                  {candidate.locator_label ?? "—"}
                                                </span>
                                                {candidate.preview && (
                                                  <Button
                                                    className="mt-1 w-fit"
                                                    variant="outline"
                                                    size="sm"
                                                    onClick={() => setDiscoveryPreview(candidate)}
                                                  >
                                                    {t("result.preview")}
                                                  </Button>
                                                )}
                                              </div>
                                            ) : "—"}
                                          </TableCell>
                                          <TableCell className="break-all whitespace-normal font-mono text-xs">
                                            {candidate.resolution_status === "resolved" ? (
                                              <div className="grid gap-1">
                                                <span>{candidate.document_ref}</span>
                                                <span>{candidate.document_version_ref}</span>
                                                <span>{candidate.processing_revision_ref ?? "—"}</span>
                                                <span>{candidate.processing_generation_ref}</span>
                                                <span>{candidate.index_generation_ref}</span>
                                              </div>
                                            ) : "—"}
                                          </TableCell>
                                          <TableCell className="break-words whitespace-normal">
                                            {candidate.resolution_status}
                                          </TableCell>
                                        </TableRow>
                                      ))}
                                    </TableBody>
                                  </Table>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                        <div>
                          <div className="font-medium">{t("audit.durableRuntimeEvents")}</div>
                          <Table className="mt-2">
                            <TableHeader>
                              <TableRow>
                                <TableHead>{t("audit.invocationId")}</TableHead>
                                <TableHead>{t("audit.eventSequence")}</TableHead>
                                <TableHead>{t("audit.eventType")}</TableHead>
                                <TableHead>{t("audit.executionStatus")}</TableHead>
                                <TableHead>{t("audit.invocationOrdinal")}</TableHead>
                                <TableHead>{t("audit.resultRef")}</TableHead>
                                <TableHead>{t("audit.errorCode")}</TableHead>
                                <TableHead>{t("audit.traceCreatedAt")}</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {selectedRuntime.events.map((event) => (
                                <TableRow key={event.event_id}>
                                  <TableCell className="font-mono text-xs">{event.event_id}</TableCell>
                                  <TableCell>{event.sequence}</TableCell>
                                  <TableCell className="font-medium">{event.event_type}</TableCell>
                                  <TableCell>{event.state}</TableCell>
                                  <TableCell>{event.invocation_ordinal ?? "—"}</TableCell>
                                  <TableCell className="font-mono text-xs">{event.result_ref ?? "—"}</TableCell>
                                  <TableCell>{event.failure_code ?? "—"}</TableCell>
                                  <TableCell>{formatDateTime(event.created_at, i18n.resolvedLanguage ?? i18n.language)}</TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              )
              )}
            </CardContent>
          </Card>
        )}

        {isEvents && (
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
                    disabled={eventsLoading}
                    onClick={() => void loadEvents()}
                  >
                    <RefreshCw aria-hidden="true" data-icon="inline-start" />
                    {t("ops.refresh")}
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {eventsLoading ? (
                <LoadingState
                  title={t("audit.loading")}
                />
              ) : eventsLoadError ? (
                <LoadErrorState
                  title={t("admin.listLoadFailed")}
                  description={eventsLoadError}
                  retryLabel={t("admin.retry")}
                  onRetry={() => void loadEvents()}
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
                            i18n.resolvedLanguage ?? i18n.language,
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
                              i18n.resolvedLanguage ?? i18n.language,
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
        )}
    </section>
    <Dialog
      open={Boolean(discoveryPreview)}
      onOpenChange={(open) => {
        if (!open) setDiscoveryPreview(null);
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
        setDeclaredEvidence(null);
        setDeclaredEvidenceRoute(null);
      }}
    />
    </>
  );
}

function ReasoningTracePanel({ trace }: { trace: ReasoningTrace }) {
  const { t } = useTranslation();
  return (
    <section className="flex flex-col gap-3" aria-labelledby="reasoning-trace-title">
      <div>
        <div id="reasoning-trace-title" className="font-medium">
          {t("audit.reasoningTrace")}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("audit.processScoreDisclaimer")}
        </p>
      </div>
      <div className="grid gap-3 rounded-md border p-3 sm:grid-cols-2">
        <AuditField label={t("audit.reasoningTraceStatus")} value={trace.status} />
        <AuditField
          label={t("audit.reasoningTermination")}
          value={trace.termination_reason ?? "—"}
        />
        <AuditField
          label={t("audit.reasoningTraceRevision")}
          value={String(trace.trace_revision)}
        />
        <AuditField label={t("audit.reasoningTraceDigest")} value={trace.trace_digest} />
        <AuditField
          label={t("audit.reasoningParentDigest")}
          value={trace.parent_trace_digest ?? "—"}
        />
        <AuditField label={t("audit.reasoningSchemaVersion")} value={trace.schema_version} />
      </div>
      <div>
        <div className="text-sm font-medium">{t("audit.reasoningPlan")}</div>
        {trace.plans.length > 0 ? (
          <div className="mt-2 flex flex-col gap-3">
            {trace.plans.map((plan) => (
              <div key={plan.generation} className="rounded-md border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">
                    {t("audit.reasoningPlanGeneration", { generation: plan.generation })}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {t("audit.reasoningParentGeneration", {
                      generation: plan.parent_generation ?? "—",
                    })}
                  </span>
                </div>
                <p className="mt-2 text-sm">{plan.next_objective}</p>
                <p className="mt-1 text-xs text-muted-foreground">{plan.completion_condition}</p>
                <ol className="mt-2 flex flex-col gap-2">
                  {plan.items.map((item) => (
                    <li key={item.item_id} className="flex items-start gap-2 rounded-md border p-2 text-sm">
                      <Badge variant="outline">{item.status}</Badge>
                      <span>{item.summary}</span>
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">{t("audit.notReported")}</p>
        )}
      </div>
      <div>
        <div className="text-sm font-medium">{t("audit.provisionalEvidenceChecks")}</div>
        {trace.provisional_evidence_checks.length === 0 ? (
          <p className="mt-1 text-sm text-muted-foreground">{t("audit.notReported")}</p>
        ) : (
          <ol className="mt-2 flex flex-col gap-2">
            {trace.provisional_evidence_checks.map((check) => (
              <li key={check.ordinal} className="rounded-md border p-2 text-sm">
                <span className="font-medium">
                  {t("audit.provisionalEvidenceCheck", {
                    ordinal: check.ordinal,
                    kind: check.candidate_kind,
                    consistency: check.consistency,
                    disposition: check.candidate_disposition,
                  })}
                </span>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("audit.provisionalEvidenceLinks", {
                    evaluation: check.linked_evaluation_cycle ?? "—",
                    reason: check.reason_code,
                    answer: check.answer_digest.slice(0, 12),
                    subset: check.declared_subset_digest.slice(0, 12),
                    images: check.visual_image_digests.length,
                  })}
                </p>
              </li>
            ))}
          </ol>
        )}
      </div>
      <div>
        <div className="text-sm font-medium">{t("audit.reasoningEvaluations")}</div>
        {trace.evaluations.length === 0 ? (
          <p className="mt-1 text-sm text-muted-foreground">{t("audit.notReported")}</p>
        ) : (
          <div className="mt-2 flex flex-col gap-3">
            {trace.evaluations.map((evaluation) => (
              <div key={evaluation.cycle} className="rounded-md border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">
                    {t("audit.reasoningCycle", { cycle: evaluation.cycle })}
                  </Badge>
                  <StatusBadge
                    semantic={evaluation.verdict === "unavailable" ? "attention" : "inactive"}
                    label={evaluation.verdict}
                  />
                  {evaluation.score ? (
                    <Badge>{t("audit.processScoreTotal", { total: evaluation.score.total })}</Badge>
                  ) : null}
                </div>
                <p className="mt-2 text-sm">
                  {evaluation.summary ?? evaluation.unavailable_reason ?? t("audit.notReported")}
                </p>
                {evaluation.finding_codes.length > 0 ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {evaluation.finding_codes.join(", ")}
                  </p>
                ) : null}
                {evaluation.score ? (
                  <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-5">
                    <AuditTraceValue label={t("audit.scorePlanCoverage")} value={String(evaluation.score.plan_coverage)} />
                    <AuditTraceValue label={t("audit.scoreEvidenceHandling")} value={String(evaluation.score.evidence_handling)} />
                    <AuditTraceValue label={t("audit.scoreConflictHandling")} value={String(evaluation.score.conflict_handling)} />
                    <AuditTraceValue label={t("audit.scoreGapResolution")} value={String(evaluation.score.gap_resolution)} />
                    <AuditTraceValue label={t("audit.scoreRevisionCompletion")} value={String(evaluation.score.revision_completion)} />
                  </dl>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>
      {trace.corrections.length > 0 ? (
        <div>
          <div className="text-sm font-medium">{t("audit.reasoningCorrections")}</div>
          <ol className="mt-2 flex flex-col gap-2">
            {trace.corrections.map((correction) => (
              <li key={correction.cycle} className="rounded-md border p-2 text-sm">
                <span className="font-medium">
                  {t("audit.reasoningCorrection", {
                    cycle: correction.cycle,
                    kind: correction.kind,
                  })}
                </span>
                <p className="mt-1">{correction.summary}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("audit.reasoningCorrectionLinks", {
                    trigger: correction.triggering_evaluation,
                    result: correction.result_evaluation,
                    generation: correction.plan_generation ?? "—",
                    tools:
                      correction.tool_invocation_start === null
                        ? "—"
                        : `${correction.tool_invocation_start}–${correction.tool_invocation_end}`,
                  })}
                </p>
                {correction.addressed_finding_codes.length > 0 ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {correction.addressed_finding_codes.join(", ")}
                  </p>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {trace.limit_finalization ? (
        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">{t("audit.reasoningLimitFinalization")}</div>
          <p className="mt-1">{trace.limit_finalization.summary}</p>
        </div>
      ) : null}
    </section>
  );
}

function AuditField({ label, value }: { label: string; value: string }) {
  return <div><div className="font-medium">{label}</div><div className="break-all text-muted-foreground">{value}</div></div>;
}

function AuditTraceValue({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-all font-mono">{value}</dd>
    </div>
  );
}

function metadataFingerprint(event: AuditEvent) {
  const value = event.metadata.token_fingerprint;
  return typeof value === "string" ? value : "-";
}

function formatDateTime(value: string, locale: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "-";
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
