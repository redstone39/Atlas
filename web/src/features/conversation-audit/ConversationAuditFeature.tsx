import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { matchAppRoute, type AppRoute } from "../../shared/routes";
import { serverMessage } from "../../shared/product-ui";
import { ApiError } from "../../shared/user-messages";
import type {
  ConversationDetail,
  ConversationSummary,
  ConversationTurn,
  DeclaredEvidencePreview,
} from "../workspace/index";
import { conversationAuditApi } from "./api";
import {
  ConversationAuditPresentation,
} from "./ConversationAuditPresentation";
import type { DiscoveryPreview } from "./AuditPresentationUtils";
import type { AuditEvent, RuntimeTraceDetail } from "./types";
export function ConversationAuditFeature({
  route,
  onNavigate,
}: {
  route: AppRoute;
  onNavigate: (route: AppRoute) => void;
}) {
  const { t } = useTranslation();
  const routeMatch = matchAppRoute(route);
  const isLanding = route === "/admin/audit";
  const isConversationDirectory =
    routeMatch.kind === "admin-audit-section" &&
    routeMatch.section === "conversations";
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

  return (
    <ConversationAuditPresentation
      route={route}
      onNavigate={onNavigate}
      isLanding={isLanding}
      isConversationDirectory={isConversationDirectory}
      isEvents={isEvents}
      isConversationDetail={isConversationDetail}
      isRuntimeDetail={isRuntimeDetail}
      resourceUnavailable={resourceUnavailable}
      loading={loading}
      loadError={loadError}
      events={events}
      eventsLoading={eventsLoading}
      eventsLoadError={eventsLoadError}
      conversations={conversations}
      nextConversationCursor={nextConversationCursor}
      conversationPageError={conversationPageError}
      moreConversationsLoading={moreConversationsLoading}
      conversationHistoryRefreshing={conversationHistoryRefreshing}
      selectedConversationId={selectedConversationId}
      selectedConversation={selectedConversation}
      conversationLoading={conversationLoading}
      conversationError={conversationError}
      selectedRuntime={selectedRuntime}
      selectedRuntimeTurn={selectedRuntimeTurn}
      runtimeLoading={runtimeLoading}
      runtimeError={runtimeError}
      discoveryPreview={discoveryPreview}
      visibleDeclaredEvidence={visibleDeclaredEvidence}
      visibleDeclaredEvidenceLoading={visibleDeclaredEvidenceLoading}
      onLoadConversationHistory={loadConversationHistory}
      onLoadEvents={loadEvents}
      onOpenConversation={openConversation}
      onOpenRuntime={openRuntime}
      onOpenDeclaredEvidence={openDeclaredEvidence}
      onDiscoveryPreviewChange={setDiscoveryPreview}
      onCloseDeclaredEvidence={() => {
        setDeclaredEvidence(null);
        setDeclaredEvidenceRoute(null);
      }}
    />
  );
}
