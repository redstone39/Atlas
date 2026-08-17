import {
  FolderKanban,
  MessageSquarePlus,
  UsersRound,
  PanelLeft,
  Search,
  SendHorizontal,
  Sparkles,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ProductContentFrame } from "../../components/shell/ProductContentFrame";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import {
  Field,
  FieldGroup,
  FieldLabel,
} from "../../components/ui/field";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "../../components/ui/sheet";
import { Spinner } from "../../components/ui/spinner";
import { Textarea } from "../../components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "../../components/ui/toggle-group";
import {
  LoadErrorState,
  LoadingState,
  serverMessage,
} from "../../shared/product-ui";
import { cn } from "../../lib/utils";
import {
  workspaceConversationRoute,
  workspaceProjectKnowledgeRoute,
  workspaceTeamKnowledgeRoute,
  type AppRoute,
} from "../../shared/routes";
import { ApiError } from "../../shared/user-messages";
import { ConversationHistorySidebar } from "./ConversationHistorySidebar";
import { ConversationScopeSelector } from "./ConversationScopeSelector";
import {
  EvidenceViewerDialog,
  type EvidenceViewerWatermark,
} from "./EvidenceViewerDialog";
import { workspaceApi, type DeclaredEvidencePreview } from "./api";
import { ConversationThread } from "./WorkspaceConversationViews";
import {
  mergeReasoningProgress,
  mergeStreamingSegment,
  projectRuntimeStreamEvent,
  resultToTurn,
} from "./workspaceProjections";
import type {
  DocumentTagRef,
  DocumentTagSummary,
  ConversationDetail,
  ConversationSummary,
  ConversationTurn,
  ResponseSegment,
  ReasoningMode,
  ReasoningProgress,
  RuntimeStreamEvent,
  WorkspaceFeatureProps,
  TurnFeedbackValue,
} from "./types";

type TurnContext = {
  query: string;
  idempotencyKey: string;
  conversationId?: string;
  executionId?: string;
  reasoningMode: ReasoningMode;
};

type RuntimeStreamPayload = {
  segment?: ResponseSegment;
};

type WorkspaceSurface =
  | { kind: "conversation" }
  | {
      kind: "knowledge";
      scopeType: "project" | "team";
      scopeId: string | null;
    };

function scopeTagKey(ref: DocumentTagRef) {
  return `${ref.tag_type}:${ref.tag_id}`;
}

export function WorkspaceFeature({
  conversationId,
  initialKnowledgeSurface,
  session,
  onNavigate,
  onReplace,
  renderSidebarHeader,
  renderAccountMenu,
  renderKnowledgeScope,
}: WorkspaceFeatureProps) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  const [scopeOptions, setScopeOptions] = useState<DocumentTagSummary[]>([]);
  const [selectedScopeTags, setSelectedScopeTags] =
    useState<DocumentTagSummary[]>([]);
  const [scopeLoading, setScopeLoading] = useState(false);
  const [scopeLoadError, setScopeLoadError] = useState(false);
  const [scopeReloadKey, setScopeReloadKey] = useState(0);
  const [turns, setTurns] = useState<ConversationTurn[]>([]);
  const [retryContext, setRetryContext] = useState<TurnContext | null>(null);
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyLoadError, setHistoryLoadError] = useState(false);
  const [historyReloadKey, setHistoryReloadKey] = useState(0);
  const [conversationReloadKey, setConversationReloadKey] = useState(0);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationLoadError, setConversationLoadError] = useState("");
  const [pendingFeedbackTurnIds, setPendingFeedbackTurnIds] =
    useState<Set<string>>(() => new Set());
  const [reconnectingExecutionId, setReconnectingExecutionId] =
    useState<string | null>(null);
  const [runtimeProgress, setRuntimeProgress] = useState("");
  const [reasoningMode, setReasoningMode] = useState<ReasoningMode>("standard");
  const [liveReasoningTimeline, setLiveReasoningTimeline] =
    useState<ReasoningProgress[]>([]);
  const [streamingSegments, setStreamingSegments] = useState<ResponseSegment[]>([]);
  const [queryError, setQueryError] = useState("");
  const [historyExpanded, setHistoryExpanded] = useState(true);
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false);
  const workspaceSurface: WorkspaceSurface = initialKnowledgeSurface
    ? { kind: "knowledge", ...initialKnowledgeSurface }
    : { kind: "conversation" };
  const [archivingConversationId, setArchivingConversationId] =
    useState<string | null>(null);
  const [citationEvidence, setCitationEvidence] = useState<DeclaredEvidencePreview | null>(null);
  const [citationWatermark, setCitationWatermark] =
    useState<EvidenceViewerWatermark | null>(null);
  const [citationLoading, setCitationLoading] = useState(false);
  const activeSection =
    workspaceSurface.kind === "conversation"
      ? "conversation"
      : workspaceSurface.scopeType;
  const activeSidebarConversationId =
    activeSection === "conversation"
      ? activeConversation?.conversation_id ?? null
      : null;
  const newConversationActive =
    activeSection === "conversation" && activeConversation === null;
  const composerCompositionRef = useRef(false);
  const compositionEndTimerRef = useRef<number | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const conversationRequestRef = useRef<AbortController | null>(null);
  const liveTurnRequestRef = useRef<AbortController | null>(null);
  const feedbackRequestRefs = useRef<Map<string, AbortController>>(new Map());
  const currentConversationIdRef = useRef(conversationId);
  currentConversationIdRef.current = conversationId;
  const newConversationScopeVisible =
    conversationId === null && activeConversation === null;
  const scopeItems = useMemo(() => {
    const byKey = new Map<string, DocumentTagSummary>();
    for (const item of [...selectedScopeTags, ...scopeOptions]) {
      byKey.set(scopeTagKey(item), item);
    }
    return [...byKey.values()];
  }, [scopeOptions, selectedScopeTags]);
  const initialLoading = historyLoading || conversationLoading;
  const canAsk = Boolean(
    query.trim() &&
      !loading &&
      !initialLoading &&
      !reconnectingExecutionId &&
      (selectedScopeTags.length === 0 || (!scopeLoading && !scopeLoadError)),
  );

  function applyRuntimeProgress(
    event: RuntimeStreamEvent,
    eventType: string,
  ) {
    const projection = projectRuntimeStreamEvent(event, eventType);
    if (projection.phase) setRuntimeProgress(projection.phase);
    if (projection.progress) {
      setLiveReasoningTimeline((current) =>
        mergeReasoningProgress(current, projection.progress!),
      );
    }
    return projection.progress;
  }

  const openDeclaredEvidence = async (turnId: string, protectedOpenRef: string) => {
    const source = turns.find((turn) => turn.turn_id === turnId);
    if (!source) return;
    setCitationEvidence(null);
    setCitationWatermark(null);
    setCitationLoading(true);
    try {
      const preview = await workspaceApi.readDeclaredEvidencePreview(
        source.conversation_id,
        source.turn_id,
        protectedOpenRef,
      );
      setCitationEvidence(preview);
      setCitationWatermark({
        displayName: session.actor?.display_name ?? null,
        actorId: session.actor?.actor_id ?? null,
        displayedAt: new Date().toISOString(),
      });
    } catch {
      setCitationWatermark(null);
      toast.error(t("citationViewer.loadFailed"));
    } finally {
      setCitationLoading(false);
    }
  };


  useEffect(() => {
    let cancelled = false;
    setHistoryLoading(true);
    setHistoryLoadError(false);
    workspaceApi.listWorkspaceConversations()
      .then((result) => {
        if (cancelled) return;
        setConversations(result.conversations);
      })
      .catch(() => {
        if (!cancelled) {
          setHistoryLoadError(true);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setHistoryLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [historyReloadKey]);

  useEffect(() => {
    if (conversationId !== null) return;
    let cancelled = false;
    setScopeLoading(true);
    setScopeLoadError(false);
    workspaceApi.workspaceTagScope()
      .then((result) => {
        if (cancelled) return;
        setScopeOptions(result.tags);
        setSelectedScopeTags([]);
      })
      .catch(() => {
        if (!cancelled) {
          setSelectedScopeTags([]);
          setScopeLoadError(true);
        }
      })
      .finally(() => {
        if (!cancelled) setScopeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, scopeReloadKey, session.actor?.actor_id]);

  useEffect(() => () => {
    conversationRequestRef.current?.abort();
    liveTurnRequestRef.current?.abort();
    abortFeedbackRequests();
    if (compositionEndTimerRef.current !== null) {
      window.clearTimeout(compositionEndTimerRef.current);
    }
  }, []);

  function abortFeedbackRequests() {
    for (const controller of feedbackRequestRefs.current.values()) {
      controller.abort();
    }
    feedbackRequestRefs.current.clear();
    setPendingFeedbackTurnIds(new Set());
  }

  async function selectConversation(conversationId: string) {
    abortFeedbackRequests();
    cancelLiveTurnRequest();
    conversationRequestRef.current?.abort();
    const controller = new AbortController();
    conversationRequestRef.current = controller;
    setActiveConversation(null);
    setTurns([]);
    setConversationLoading(true);
    setConversationLoadError("");
    setReconnectingExecutionId(null);
    let detail;
    try {
      detail = await workspaceApi.getWorkspaceConversation(
        conversationId,
        controller.signal,
      );
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 404) {
        startNewConversation();
        onReplace("/workspace");
        toast.error(t("workspace.historyLoadFailed"));
        return;
      }
      setConversationLoadError(
        error instanceof Error ? error.message : t("workspace.historyLoadFailed"),
      );
      return;
    } finally {
      if (!controller.signal.aborted) setConversationLoading(false);
    }
    if (controller.signal.aborted) return;
    setActiveConversation(detail);
    setReasoningMode(detail.reasoning_mode ?? "standard");
    setTurns(detail.turns);
    setQueryError("");
    setStreamingSegments([]);
    setMobileHistoryOpen(false);
    const pending = [...detail.turns].reverse().find(
      (turn) =>
        turn.role === "assistant" &&
        turn.execution_status === "processing" &&
        turn.execution_id,
    );
    if (pending?.execution_id) {
      void resumeConversationExecution(
        detail.conversation_id,
        pending.execution_id,
        controller,
      );
    }
  }

  async function updateTurnFeedback(
    turn: ConversationTurn,
    feedback: TurnFeedbackValue,
  ) {
    if (
      turn.role !== "assistant" ||
      turn.feedback?.feedback === feedback ||
      feedbackRequestRefs.current.has(turn.turn_id)
    ) return;
    const selectedConversationId = turn.conversation_id;
    if (currentConversationIdRef.current !== selectedConversationId) return;

    const controller = new AbortController();
    feedbackRequestRefs.current.set(turn.turn_id, controller);
    setPendingFeedbackTurnIds((current) => new Set(current).add(turn.turn_id));
    try {
      const result = await workspaceApi.updateTurnFeedback(
        selectedConversationId,
        turn.turn_id,
        {
          feedback,
          expected_revision: turn.feedback?.revision ?? 0,
          idempotency_key: createIdempotencyKey(),
        },
        controller.signal,
      );
      if (
        controller.signal.aborted ||
        currentConversationIdRef.current !== selectedConversationId
      ) return;
      setTurns((current) => current.map((item) =>
        item.role === "assistant" && item.turn_id === turn.turn_id
          ? { ...item, feedback: result }
          : item,
      ));
      setActiveConversation((current) =>
        current?.conversation_id === selectedConversationId
          ? {
              ...current,
              turns: current.turns.map((item) =>
                item.role === "assistant" && item.turn_id === turn.turn_id
                  ? { ...item, feedback: result }
                  : item),
            }
          : current);
      toast.success(t("workspace.feedbackSaved"));
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 409) {
        toast.error(t("workspace.feedbackConflictReload"));
        await selectConversation(selectedConversationId);
        return;
      }
      toast.error(
        error instanceof ApiError
          ? serverMessage(error, t)
          : t("workspace.feedbackSaveFailed"),
      );
    } finally {
      if (feedbackRequestRefs.current.get(turn.turn_id) === controller) {
        feedbackRequestRefs.current.delete(turn.turn_id);
        setPendingFeedbackTurnIds((current) => {
          const next = new Set(current);
          next.delete(turn.turn_id);
          return next;
        });
      }
    }
  }

  async function resumeConversationExecution(
    selectedConversationId: string,
    executionId: string,
    controller: AbortController,
  ) {
    setLoading(true);
    setReconnectingExecutionId(executionId);
    setQueryError("");
    try {
      const result = await workspaceApi.reconnectConversationTurn(
        selectedConversationId,
        executionId,
        (event, eventType) => {
          if (controller.signal.aborted) return;
          const progress = applyRuntimeProgress(event, eventType);
          if (progress) {
            setTurns((current) => current.map((turn) =>
              turn.role === "assistant" && turn.execution_id === executionId
                ? {
                    ...turn,
                    reasoning_timeline: mergeReasoningProgress(
                      turn.reasoning_timeline,
                      progress,
                    ),
                  }
                : turn,
            ));
          }
          if (eventType === "segment_delta" && (event as RuntimeStreamPayload).segment) {
            const segment = (event as RuntimeStreamPayload).segment!;
            setStreamingSegments((current) => [
              ...current,
              segment,
            ]);
            setTurns((current) => current.map((turn) =>
              turn.role === "assistant" && turn.execution_id === executionId
                ? mergeStreamingSegment(turn, segment)
                : turn,
            ));
          }
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      const assistant = resultToTurn(result);
      setTurns((current) =>
        current.map((turn) =>
          turn.role === "assistant" && turn.turn_id === assistant.turn_id
            ? assistant
            : turn,
        ),
      );
      setConversations((current) =>
        current.map((conversation) =>
          conversation.conversation_id === selectedConversationId
            ? {
                ...conversation,
                updated_at: result.created_at,
                last_turn_status: result.execution_status,
              }
            : conversation,
        ),
      );
      setReconnectingExecutionId(null);
    } catch (error) {
      if (controller.signal.aborted) return;
      setQueryError(
        error instanceof Error ? error.message : t("workspace.queryFailedDescription"),
      );
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
        setRuntimeProgress("");
        setStreamingSegments([]);
        setLiveReasoningTimeline([]);
      }
    }
  }

  function retryConversationRecovery() {
    conversationRequestRef.current?.abort();
    setActiveConversation(null);
    setTurns([]);
    setReconnectingExecutionId(null);
    setReasoningMode("standard");
    setQueryError("");
    setConversationReloadKey((current) => current + 1);
  }

  useEffect(() => {
    if (!conversationId) {
      if (!initialKnowledgeSurface) startNewConversation(false);
      return;
    }
    if (activeConversation?.conversation_id === conversationId) return;
    void selectConversation(conversationId);
  }, [
    conversationId,
    conversationReloadKey,
    initialKnowledgeSurface?.scopeId,
    initialKnowledgeSurface?.scopeType,
  ]);

  useEffect(() => {
    if (
      !conversationId &&
      !activeConversation &&
      !initialLoading &&
      !reconnectingExecutionId
    ) {
      composerRef.current?.focus();
    }
  }, [
    conversationId,
    activeConversation,
    initialLoading,
    reconnectingExecutionId,
  ]);

  function startNewConversation(clearScopeSelection = true) {
    cancelLiveTurnRequest();
    abortFeedbackRequests();
    conversationRequestRef.current?.abort();
    conversationRequestRef.current = null;
    setActiveConversation(null);
    setTurns([]);
    if (clearScopeSelection) {
      setSelectedScopeTags([]);
    }
    setQuery("");
    setQueryError("");
    setRetryContext(null);
    setReconnectingExecutionId(null);
    setMobileHistoryOpen(false);
  }

  function openNewConversation() {
    startNewConversation();
    setScopeReloadKey((current) => current + 1);
    if (conversationId || initialKnowledgeSurface) {
      onNavigate("/workspace");
    }
    composerRef.current?.focus();
  }

  async function openConversation(conversationId: string) {
    setMobileHistoryOpen(false);
    onNavigate(workspaceConversationRoute(conversationId));
  }

  async function archiveConversation(conversation: ConversationSummary) {
    if (
      conversation.last_turn_status === "processing" ||
      archivingConversationId === conversation.conversation_id
    ) return;
    setArchivingConversationId(conversation.conversation_id);
    try {
      await workspaceApi.archiveWorkspaceConversation(
        conversation.conversation_id,
        createIdempotencyKey(),
      );
      setConversations((current) => current.filter(
        (item) => item.conversation_id !== conversation.conversation_id,
      ));
      if (currentConversationIdRef.current === conversation.conversation_id) {
        startNewConversation();
        onReplace("/workspace");
      }
      toast.success(t("workspace.deleteConversationSucceeded"));
    } catch {
      toast.error(t("workspace.deleteConversationFailed"));
    } finally {
      setArchivingConversationId(null);
    }
  }

  function openKnowledgeRoute(route: AppRoute) {
    setMobileHistoryOpen(false);
    cancelLiveTurnRequest();
    if (route === "/projects") {
      onNavigate("/workspace/projects");
      return;
    }
    if (route === "/teams") {
      onNavigate("/workspace/teams");
      return;
    }
    if (route.startsWith("/projects/")) {
      const projectId = route.slice("/projects/".length, -"/knowledge".length);
      onNavigate(workspaceProjectKnowledgeRoute(decodeURIComponent(projectId)));
      return;
    }
    if (route.startsWith("/teams/")) {
      const teamId = route.slice("/teams/".length, -"/knowledge".length);
      onNavigate(workspaceTeamKnowledgeRoute(decodeURIComponent(teamId)));
      return;
    }
    onNavigate(route);
  }

  function cancelLiveTurnRequest() {
    liveTurnRequestRef.current?.abort();
    liveTurnRequestRef.current = null;
    setLoading(false);
    setRuntimeProgress("");
    setStreamingSegments([]);
    setLiveReasoningTimeline([]);
  }

  async function ensureConversation(submittedQuery: string, signal: AbortSignal) {
    if (activeConversation) return activeConversation;
    const scopeSnapshot = selectedScopeTags.map<DocumentTagRef>(
      ({ tag_type, tag_id }) => ({ tag_type, tag_id }),
    );
    const detail = await workspaceApi.createWorkspaceConversation(
      submittedQuery.slice(0, 64) || t("workspace.newConversation"),
      i18n.language === "zh-TW" ? "zh-TW" : "en",
      scopeSnapshot,
      signal,
    );
    if (signal.aborted) throw new DOMException("The operation was aborted.", "AbortError");
    setActiveConversation(detail);
    setTurns(detail.turns);
    setConversations((current) => [
      {
        conversation_id: detail.conversation_id,
        owner_actor_id: detail.owner_actor_id,
        title: detail.title,
        status: detail.status,
        response_language: detail.response_language,
        reasoning_mode: detail.reasoning_mode ?? "standard",
        created_at: detail.created_at,
        updated_at: detail.updated_at,
        last_turn_status: null,
      },
      ...current.filter((conversation) => conversation.conversation_id !== detail.conversation_id),
    ]);
    onReplace(workspaceConversationRoute(detail.conversation_id));
    return detail;
  }

  async function askQuestion(
    nextQuery = query,
    existingKey?: string,
    existingReasoningMode?: ReasoningMode,
  ) {
    const submittedQuery = nextQuery.trim();
    const submittedReasoningMode = existingReasoningMode ?? reasoningMode;
    const submittedContext: TurnContext = {
      query: submittedQuery,
      idempotencyKey: existingKey ?? createIdempotencyKey(),
      reasoningMode: submittedReasoningMode,
    };
    liveTurnRequestRef.current?.abort();
    const controller = new AbortController();
    liveTurnRequestRef.current = controller;
    setLoading(true);
    setQueryError("");
    setRetryContext(submittedContext);
    let acceptedRequestId: string | null = null;
    let submittedConversationId: string | null = null;
    try {
      const conversation = await ensureConversation(submittedQuery, controller.signal);
      if (controller.signal.aborted) return;
      submittedConversationId = conversation.conversation_id;
      submittedContext.conversationId = conversation.conversation_id;
      setRetryContext({ ...submittedContext });
      const userTurn: ConversationTurn = {
        turn_id: `pending-${Date.now()}`,
        conversation_id: conversation.conversation_id,
        role: "user",
        input_text: submittedQuery,
        answer_text: null,
        execution_status: "completed",
        reasoning_mode: submittedReasoningMode,
        reasoning_timeline: [],
        response_kind: "dialogue",
        verification_status: null,
        evidence_review_status: null,
        evidence_review_reason_codes: [],
        assessment_state: null,
        assessment_reason_code: null,
        assessment_input_digest: null,
        assessment_output_digest: null,
        content_state: "available",
        refusal_code: null,
        user_reason: t("workspace.submitted"),
        citations: [],
        model_claimed_evidence: [],
        response_segments: [],
        validation_state: "not_applicable",
        used_knowledge_refs: [],
        source_turn_id: null,
        execution_id: null,
        retryable: false,
        runtime_trace_id: null,
        audit_event_ref: null,
        created_at: new Date().toISOString(),
        feedback: null,
      };
      if (!existingKey) setTurns((current) => [...current, userTurn]);
      setQuery("");
      const result = await workspaceApi.streamConversationTurn(
        conversation.conversation_id,
        submittedQuery,
        submittedContext.idempotencyKey,
        submittedReasoningMode,
        (event, eventType) => {
          if (controller.signal.aborted) return;
          if (eventType === "turn_accepted") {
            acceptedRequestId = event.execution_id ?? null;
            submittedContext.executionId = event.execution_id;
            setRetryContext({ ...submittedContext });
            setConversations((current) =>
              current.map((conversationItem) =>
                conversationItem.conversation_id === conversation.conversation_id
                  ? { ...conversationItem, last_turn_status: "processing" }
                  : conversationItem,
              ),
            );
            setActiveConversation((current) => current
              ? { ...current, reasoning_mode: submittedReasoningMode }
              : current);
            setReasoningMode(submittedReasoningMode);
          }
          applyRuntimeProgress(event, eventType);
          if (eventType === "segment_delta" && (event as RuntimeStreamPayload).segment) {
            setStreamingSegments((current) => [...current, (event as RuntimeStreamPayload).segment!]);
          }
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      submittedContext.executionId = result.execution_id;
      setRetryContext({ ...submittedContext });
      const assistantTurn = resultToTurn(result);
      setTurns((current) => [...current, assistantTurn]);
      setConversations((current) =>
        current.map((conversationItem) =>
          conversationItem.conversation_id === conversation.conversation_id
            ? {
                ...conversationItem,
                updated_at: result.created_at,
                last_turn_status: result.execution_status,
              }
            : conversationItem,
        ),
      );
    } catch (error) {
      if (acceptedRequestId && submittedConversationId) {
        try {
          const recovered = await workspaceApi.reconnectConversationTurn(
            submittedConversationId,
            acceptedRequestId,
            undefined,
            controller.signal,
          );
          if (controller.signal.aborted) return;
          setTurns((current) => [...current, resultToTurn(recovered)]);
          return;
        } catch {
          // Show the original transport error with the same-key retry affordance.
        }
      }
      if (controller.signal.aborted) return;
      const message = error instanceof Error ? error.message : t("workspace.queryFailedDescription");
      setQueryError(message);
    } finally {
      if (liveTurnRequestRef.current === controller) {
        liveTurnRequestRef.current = null;
        setLoading(false);
        setRuntimeProgress("");
        setStreamingSegments([]);
        setLiveReasoningTimeline([]);
      }
    }
  }

  async function retryFailedTurn(turn: ConversationTurn) {
    if (!turn.source_turn_id || !activeConversation) return;
    liveTurnRequestRef.current?.abort();
    const controller = new AbortController();
    liveTurnRequestRef.current = controller;
    setLoading(true);
    setQueryError("");
    try {
      const result = await workspaceApi.retryConversationTurn(
        activeConversation.conversation_id,
        turn.source_turn_id,
        createIdempotencyKey(),
        (event, eventType) => {
          if (controller.signal.aborted) return;
          applyRuntimeProgress(event, eventType);
          if (eventType === "segment_delta" && (event as RuntimeStreamPayload).segment) setStreamingSegments((current) => [...current, (event as RuntimeStreamPayload).segment!]);
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setTurns((current) => current.map((item) => item.turn_id === turn.turn_id ? resultToTurn(result) : item));
    } catch (error) {
      if (controller.signal.aborted) return;
      setQueryError(error instanceof Error ? error.message : t("workspace.queryFailedDescription"));
    } finally {
      if (liveTurnRequestRef.current === controller) {
        liveTurnRequestRef.current = null;
        setLoading(false);
        setRuntimeProgress("");
        setStreamingSegments([]);
        setLiveReasoningTimeline([]);
      }
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key !== "Enter" ||
      event.shiftKey ||
      event.altKey ||
      composerCompositionRef.current ||
      event.nativeEvent.isComposing ||
      event.nativeEvent.keyCode === 229
    ) return;
    event.preventDefault();
    if (canAsk) {
      void askQuestion();
    }
  }

  function handleComposerCompositionStart() {
    if (compositionEndTimerRef.current !== null) {
      window.clearTimeout(compositionEndTimerRef.current);
      compositionEndTimerRef.current = null;
    }
    composerCompositionRef.current = true;
  }

  function handleComposerCompositionEnd() {
    if (compositionEndTimerRef.current !== null) {
      window.clearTimeout(compositionEndTimerRef.current);
    }
    compositionEndTimerRef.current = window.setTimeout(() => {
      composerCompositionRef.current = false;
      compositionEndTimerRef.current = null;
    }, 0);
  }

  return (
    <>
      <Sheet open={mobileHistoryOpen} onOpenChange={setMobileHistoryOpen}>
        <SheetContent
          side="left"
          className="w-[20rem] max-w-[85vw] gap-0 p-0"
          showCloseButton={false}
        >
          <SheetHeader className="sr-only">
            <SheetTitle>{t("workspace.conversations")}</SheetTitle>
            <SheetDescription>{t("workspace.historyDescription")}</SheetDescription>
          </SheetHeader>
          <ConversationHistorySidebar
            className="min-h-0 flex-1"
            header={renderSidebarHeader({
              presentation: "full",
              onOpenWorkspace: openNewConversation,
            })}
            onOpenProjects={() => openKnowledgeRoute("/projects")}
            onOpenTeams={() => openKnowledgeRoute("/teams")}
            activeSection={activeSection}
            conversations={conversations}
            activeConversationId={activeSidebarConversationId}
            initialLoading={historyLoading}
            loadError={historyLoadError}
            loading={loading}
            archivingConversationId={archivingConversationId}
            onSelect={openConversation}
            onDelete={archiveConversation}
            onNew={openNewConversation}
            onRetryHistory={() => setHistoryReloadKey((current) => current + 1)}
            footer={
              renderAccountMenu({ presentation: "full", className: "w-full" })
            }
          />
        </SheetContent>
      </Sheet>
      <section className="flex h-dvh min-h-[32rem] w-full overflow-hidden bg-background">
        <aside
          data-slot="workspace-context-sidebar"
          aria-hidden={mobileHistoryOpen || undefined}
          className={cn(
            "hidden min-h-0 shrink-0 overflow-hidden bg-muted/20 transition-[width] duration-200 md:flex",
            historyExpanded ? "w-72 border-r" : "w-14 border-r",
          )}
        >
          {historyExpanded ? (
            <ConversationHistorySidebar
              className="w-72"
              header={renderSidebarHeader({
                presentation: "full",
                onOpenWorkspace: openNewConversation,
                onCollapseSidebar: () => setHistoryExpanded(false),
              })}
              onOpenProjects={() => openKnowledgeRoute("/projects")}
              onOpenTeams={() => openKnowledgeRoute("/teams")}
              activeSection={activeSection}
              conversations={conversations}
              activeConversationId={activeSidebarConversationId}
              initialLoading={historyLoading}
              loadError={historyLoadError}
              loading={loading}
              archivingConversationId={archivingConversationId}
              onSelect={openConversation}
              onDelete={archiveConversation}
              onNew={openNewConversation}
              onRetryHistory={() => setHistoryReloadKey((current) => current + 1)}
              footer={
                renderAccountMenu({ presentation: "full", className: "w-full" })
              }
            />
          ) : (
            <div className="flex h-full w-14 flex-col">
              {renderSidebarHeader({
                presentation: "compact",
                onOpenWorkspace: () => setHistoryExpanded(true),
              })}
              <div className="flex flex-col gap-1 p-2">
                <Button
                  variant={newConversationActive ? "secondary" : "ghost"}
                  size="icon-sm"
                  onClick={openNewConversation}
                  aria-label={t("workspace.newConversation")}
                  title={t("workspace.newConversation")}
                  aria-current={newConversationActive ? "page" : undefined}
                >
                  <MessageSquarePlus />
                </Button>
                <Button
                  variant={activeSection === "project" ? "secondary" : "ghost"}
                  size="icon-sm"
                  onClick={() => openKnowledgeRoute("/projects")}
                  aria-label={t("nav.projects")}
                  title={t("nav.projects")}
                  aria-current={activeSection === "project" ? "page" : undefined}
                >
                  <FolderKanban />
                </Button>
                <Button
                  variant={activeSection === "team" ? "secondary" : "ghost"}
                  size="icon-sm"
                  onClick={() => openKnowledgeRoute("/teams")}
                  aria-label={t("nav.teams")}
                  title={t("nav.teams")}
                  aria-current={activeSection === "team" ? "page" : undefined}
                >
                  <UsersRound />
                </Button>
              </div>
              <div className="min-h-0 flex-1" />
              <div
                data-slot="contextual-sidebar-footer"
                className="flex shrink-0 justify-center border-t p-2"
              >
                {renderAccountMenu({
                  presentation: "compact",
                  menuSide: "right",
                  menuAlign: "end",
                })}
              </div>
            </div>
          )}
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <div
            data-slot="workspace-conversation-controls"
            className="flex min-h-14 items-center gap-2 px-3 md:hidden"
          >
            <Button
              variant="ghost"
              size="icon-sm"
              className="md:hidden"
              onClick={() => setMobileHistoryOpen(true)}
              aria-label={t("workspace.openHistory")}
              title={t("workspace.openHistory")}
            >
              <PanelLeft />
            </Button>
          </div>
          {workspaceSurface.kind === "knowledge" && (
            <ProductContentFrame
              dataSlot="workspace-knowledge-content"
              className="min-h-0"
            >
              {renderKnowledgeScope({
                scopeType: workspaceSurface.scopeType,
                scopeId: workspaceSurface.scopeId,
                onNavigate: openKnowledgeRoute,
              })}
            </ProductContentFrame>
          )}
          {workspaceSurface.kind === "conversation" && (
            <h1 className="sr-only">{t("workspace.title")}</h1>
          )}
          <div
            className={cn(
              "min-h-0 flex-1",
              workspaceSurface.kind !== "conversation" && "hidden",
            )}
          >
            {initialLoading ? (
              <div className="p-3 md:p-4">
                <LoadingState
                  title={t("workspace.initialLoadingTitle")}
                />
              </div>
            ) : conversationLoadError ? (
              <div className="p-3 md:p-4">
                <Alert variant="destructive">
                  <Search />
                  <AlertTitle>{t("workspace.historyLoadErrorTitle")}</AlertTitle>
                  <AlertDescription>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="mt-2"
                      onClick={() => setConversationReloadKey((current) => current + 1)}
                    >
                      {t("workspace.historyRetry")}
                    </Button>
                  </AlertDescription>
                </Alert>
              </div>
            ) : (
              <ConversationThread
                turns={turns}
                loading={loading}
                locale={i18n.language}
                onOpenDeclaredEvidence={openDeclaredEvidence}
                onRetry={retryFailedTurn}
                onFeedbackChange={updateTurnFeedback}
                pendingFeedbackTurnIds={pendingFeedbackTurnIds}
                runtimeProgress={runtimeProgress}
                liveReasoningTimeline={liveReasoningTimeline}
                streamingSegments={streamingSegments}
              />
            )}
          </div>
          <div
            data-slot="workspace-composer"
            className={cn(
              "bg-background p-3 md:p-4",
              workspaceSurface.kind === "knowledge" && "hidden",
            )}
          >
            {queryError && (
              <Alert variant="destructive" className="mb-3">
                <Search />
                <AlertTitle>{t("workspace.queryFailedTitle")}</AlertTitle>
                <AlertDescription className="flex flex-col gap-3">
                  <span>{serverMessage(queryError, t)}</span>
                  {(retryContext || reconnectingExecutionId) && (
                    <>
                      {retryContext && (
                        <span className="break-words text-muted-foreground">
                          {t("workspace.queryFailedContext", {
                            question: retryContext.query,
                          })}
                        </span>
                      )}
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="w-fit"
                        onClick={() =>
                          retryContext
                            ? askQuestion(
                                retryContext.query,
                                retryContext.idempotencyKey,
                                retryContext.reasoningMode,
                              )
                            : retryConversationRecovery()
                        }
                        disabled={loading}
                      >
                        {t("workspace.retryQuery")}
                      </Button>
                    </>
                  )}
                </AlertDescription>
              </Alert>
            )}
            {newConversationScopeVisible && scopeLoadError && (
              <div className="mb-3">
                <LoadErrorState
                  title={t("admin.listLoadFailed")}
                  description={t("admin.resourceUnavailableDescription")}
                  retryLabel={t("admin.retry")}
                  onRetry={() => setScopeReloadKey((current) => current + 1)}
                />
              </div>
            )}
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="message" className="sr-only">
                  {t("workspace.message")}
                </FieldLabel>
                <div
                  data-slot="message-composer"
                  className="overflow-hidden rounded-2xl border border-border/80 bg-card shadow-sm transition-[border-color,box-shadow] focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/15"
                >
                  <Textarea
                    ref={composerRef}
                    id="message"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    onKeyDown={handleComposerKeyDown}
                    onCompositionStart={handleComposerCompositionStart}
                    onCompositionEnd={handleComposerCompositionEnd}
                    placeholder={t("workspace.supportedQuestion")}
                    aria-describedby="message-help"
                    disabled={initialLoading || Boolean(reconnectingExecutionId)}
                    className="max-h-44 min-h-24 resize-none rounded-none border-0 bg-transparent px-4 py-3.5 shadow-none focus-visible:border-transparent focus-visible:ring-0 dark:bg-transparent"
                  />
                  <div
                    data-slot="message-composer-controls"
                    className="flex min-h-13 flex-wrap items-center gap-2 border-t border-border/60 bg-muted/25 px-2.5 py-2 sm:px-3"
                  >
                    {newConversationScopeVisible && (
                      <ConversationScopeSelector
                        items={scopeItems}
                        value={selectedScopeTags}
                        onValueChange={setSelectedScopeTags}
                        loading={scopeLoading}
                        disabled={
                          loading ||
                          initialLoading ||
                          scopeLoading ||
                          Boolean(scopeLoadError) ||
                          Boolean(reconnectingExecutionId)
                        }
                      />
                    )}
                    <div
                      data-slot="reasoning-controls"
                      className="ml-auto flex min-w-0 items-center gap-2"
                    >
                      <span className="hidden items-center gap-1.5 text-xs font-medium text-muted-foreground sm:flex">
                        <Sparkles className="size-3.5" aria-hidden="true" />
                        {t("workspace.reasoningMode")}
                      </span>
                      <ToggleGroup
                        type="single"
                        variant="outline"
                        size="sm"
                        value={reasoningMode}
                        onValueChange={(value) => {
                          if (value === "standard" || value === "deep") {
                            setReasoningMode(value);
                          }
                        }}
                        disabled={loading || initialLoading || Boolean(reconnectingExecutionId)}
                        aria-label={t("workspace.reasoningMode")}
                        className="rounded-lg bg-background/80"
                      >
                        <ToggleGroupItem
                          value="standard"
                          aria-label={t("workspace.reasoningModeOption.standard")}
                        >
                          {t("workspace.reasoningModeStandard")}
                        </ToggleGroupItem>
                        <ToggleGroupItem
                          value="deep"
                          aria-label={t("workspace.reasoningModeOption.deep")}
                        >
                          {t("workspace.reasoningModeDeep")}
                        </ToggleGroupItem>
                      </ToggleGroup>
                      <Button
                        type="button"
                        className="shrink-0 rounded-xl px-3 sm:px-4"
                        onClick={() => askQuestion()}
                        disabled={!canAsk}
                        aria-label={t("workspace.send")}
                        title={t("workspace.send")}
                      >
                        {loading ? (
                          <Spinner data-icon="inline-start" />
                        ) : (
                          <SendHorizontal data-icon="inline-start" />
                        )}
                      </Button>
                    </div>
                    <p id="message-help" className="sr-only">
                      {t("workspace.messageHelp")}
                    </p>
                  </div>
                </div>
              </Field>
            </FieldGroup>
          </div>
          {workspaceSurface.kind === "conversation" && (
            <EvidenceViewerDialog
              evidence={citationEvidence}
              loading={citationLoading}
              onClose={() => {
                setCitationEvidence(null);
                setCitationWatermark(null);
              }}
              watermark={citationWatermark}
            />
          )}
        </div>
      </section>
    </>
  );
}

function createIdempotencyKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export { MessageSources } from "./WorkspaceConversationViews";
export { claimsInPresentationOrder, sliceCodePoints } from "./workspaceProjections";
