import {
  BookOpen,
  Ellipsis,
  MessageSquarePlus,
  PanelLeft,
  Search,
  SendHorizontal,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type KeyboardEvent,
  type ReactNode,
  type SetStateAction,
} from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Bubble, BubbleContent } from "../../components/ui/bubble";
import { Button } from "../../components/ui/button";
import { Badge } from "../../components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { Checkbox } from "../../components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../../components/ui/dialog";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
} from "../../components/ui/empty";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import {
  Message,
  MessageContent,
  MessageGroup,
  MessageHeader,
} from "../../components/ui/message";
import {
  MessageScroller,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "../../components/ui/message-scroller";
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
  StatusBadge,
  conversationTurnStatusPresentation,
  serverMessage,
} from "../../shared/product-ui";
import { cn } from "../../lib/utils";
import { workspaceConversationRoute } from "../../shared/routes";
import { ApiError } from "../../shared/user-messages";
import { AnswerEvidenceSummary } from "./AnswerEvidenceSummary";
import { AnswerMarkdown } from "./AnswerMarkdown";
import {
  EvidenceViewerDialog,
  type EvidenceViewerWatermark,
} from "./EvidenceViewerDialog";
import {
  joinResponseSegmentMarkdown,
  workspaceApi,
  type DeclaredEvidencePreview,
} from "./api";
import { ReasoningTimeline } from "./ReasoningTimeline";
import type {
  CitationCard,
  DocumentTagRef,
  DocumentTagSummary,
  ConversationDetail,
  ConversationSummary,
  ConversationTurn,
  ConversationTurnResult,
  ResponseSegment,
  ReasoningMode,
  ReasoningProgress,
  RuntimeStreamEvent,
  WorkspaceFeatureProps,
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
  citation?: CitationCard;
};

function scopeTagKey(ref: DocumentTagRef) {
  return `${ref.tag_type}:${ref.tag_id}`;
}

export function WorkspaceFeature({
  activeView,
  conversationId,
  session,
  onNavigate,
  onReplace,
  libraryContent,
  renderSidebarHeader,
  renderAccountMenu,
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
  const [archivingConversationId, setArchivingConversationId] =
    useState<string | null>(null);
  const [citationEvidence, setCitationEvidence] = useState<DeclaredEvidencePreview | null>(null);
  const [citationWatermark, setCitationWatermark] =
    useState<EvidenceViewerWatermark | null>(null);
  const [citationLoading, setCitationLoading] = useState(false);
  const composerCompositionRef = useRef(false);
  const compositionEndTimerRef = useRef<number | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const conversationRequestRef = useRef<AbortController | null>(null);
  const liveTurnRequestRef = useRef<AbortController | null>(null);
  const currentConversationIdRef = useRef(conversationId);
  currentConversationIdRef.current = conversationId;
  const newConversationScopeVisible =
    activeView === "/workspace" &&
    conversationId === null &&
    activeConversation === null;
  const scopeItems = useMemo(() => {
    const byKey = new Map<string, DocumentTagSummary>();
    for (const item of [...selectedScopeTags, ...scopeOptions]) {
      byKey.set(scopeTagKey(item), item);
    }
    return [...byKey.values()];
  }, [scopeOptions, selectedScopeTags]);
  const initialLoading = historyLoading || conversationLoading;
  const canAsk = Boolean(
    query.trim() && !loading && !initialLoading && !reconnectingExecutionId,
  );

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
    if (activeView !== "/workspace" || conversationId !== null) return;
    let cancelled = false;
    setScopeLoading(true);
    setScopeLoadError(false);
    workspaceApi.workspaceTagScope()
      .then((result) => {
        if (!cancelled) setScopeOptions(result.tags);
      })
      .catch(() => {
        if (!cancelled) setScopeLoadError(true);
      })
      .finally(() => {
        if (!cancelled) setScopeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeView, conversationId, scopeReloadKey]);

  useEffect(() => () => {
    conversationRequestRef.current?.abort();
    liveTurnRequestRef.current?.abort();
    if (compositionEndTimerRef.current !== null) {
      window.clearTimeout(compositionEndTimerRef.current);
    }
  }, []);

  async function selectConversation(conversationId: string) {
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
          const progress = captureRuntimeProgress(
            event,
            eventType,
            setRuntimeProgress,
            setLiveReasoningTimeline,
          );
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
    if (activeView === "/library") {
      cancelLiveTurnRequest();
      return;
    }
    if (!conversationId) {
      startNewConversation();
      return;
    }
    if (activeConversation?.conversation_id === conversationId) return;
    void selectConversation(conversationId);
  }, [activeView, conversationId, conversationReloadKey]);

  useEffect(() => {
    if (
      activeView === "/workspace" &&
      !conversationId &&
      !activeConversation &&
      !initialLoading &&
      !reconnectingExecutionId
    ) {
      composerRef.current?.focus();
    }
  }, [
    activeView,
    conversationId,
    activeConversation,
    initialLoading,
    reconnectingExecutionId,
  ]);

  function startNewConversation() {
    cancelLiveTurnRequest();
    conversationRequestRef.current?.abort();
    conversationRequestRef.current = null;
    setActiveConversation(null);
    setTurns([]);
    setSelectedScopeTags([]);
    setQuery("");
    setQueryError("");
    setRetryContext(null);
    setReconnectingExecutionId(null);
    setMobileHistoryOpen(false);
  }

  function openNewConversation() {
    startNewConversation();
    setScopeReloadKey((current) => current + 1);
    if (conversationId || activeView !== "/workspace") {
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

  function openKnowledgeLibrary() {
    setMobileHistoryOpen(false);
    cancelLiveTurnRequest();
    if (activeView !== "/library") {
      onNavigate("/library");
    }
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
          captureRuntimeProgress(event, eventType, setRuntimeProgress, setLiveReasoningTimeline);
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
          captureRuntimeProgress(event, eventType, setRuntimeProgress, setLiveReasoningTimeline);
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
            knowledgeLibraryActive={activeView === "/library"}
            conversations={conversations}
            activeConversationId={activeConversation?.conversation_id ?? null}
            initialLoading={historyLoading}
            loadError={historyLoadError}
            loading={loading}
            archivingConversationId={archivingConversationId}
            onSelect={openConversation}
            onDelete={archiveConversation}
            onNew={openNewConversation}
            onOpenKnowledgeLibrary={openKnowledgeLibrary}
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
              knowledgeLibraryActive={activeView === "/library"}
              conversations={conversations}
              activeConversationId={activeConversation?.conversation_id ?? null}
              initialLoading={historyLoading}
              loadError={historyLoadError}
              loading={loading}
              archivingConversationId={archivingConversationId}
              onSelect={openConversation}
              onDelete={archiveConversation}
              onNew={openNewConversation}
              onOpenKnowledgeLibrary={openKnowledgeLibrary}
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
                  variant="ghost"
                  size="icon-sm"
                  onClick={openNewConversation}
                  aria-label={t("workspace.newConversation")}
                  title={t("workspace.newConversation")}
                >
                  <MessageSquarePlus />
                </Button>
                <Button
                  variant={activeView === "/library" ? "secondary" : "ghost"}
                  size="icon-sm"
                  onClick={openKnowledgeLibrary}
                  aria-label={t("nav.knowledgeLibrary")}
                  title={t("nav.knowledgeLibrary")}
                  aria-current={activeView === "/library" ? "page" : undefined}
                >
                  <BookOpen />
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
        {activeView === "/library" ? (
          <div className="relative min-w-0 flex-1 overflow-y-auto">
            <Button
              variant="outline"
              size="icon-sm"
              className="absolute left-3 top-3 md:hidden"
              onClick={() => setMobileHistoryOpen(true)}
              aria-label={t("workspace.openHistory")}
              title={t("workspace.openHistory")}
            >
              <PanelLeft />
            </Button>
            <div className="px-3 pb-4 pt-16 md:px-6 md:py-4">
              {libraryContent}
            </div>
          </div>
        ) : (
        <div className="flex min-w-0 flex-1 flex-col">
          <h1 className="sr-only">{t("workspace.title")}</h1>
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
          <div className="min-h-0 flex-1">
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
                runtimeProgress={runtimeProgress}
                liveReasoningTimeline={liveReasoningTimeline}
                streamingSegments={streamingSegments}
              />
            )}
          </div>
          <div data-slot="workspace-composer" className="bg-background p-3 md:p-4">
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
            {newConversationScopeVisible && (
              <div className="mb-3 flex flex-col gap-3">
                {scopeLoadError && (
                  <LoadErrorState
                    title={t("admin.listLoadFailed")}
                    description={t("admin.resourceUnavailableDescription")}
                    retryLabel={t("admin.retry")}
                    onRetry={() => setScopeReloadKey((current) => current + 1)}
                  />
                )}
                <ConversationScopeSelector
                  items={scopeItems}
                  value={selectedScopeTags}
                  onValueChange={setSelectedScopeTags}
                  loading={scopeLoading}
                  disabled={
                    loading ||
                    initialLoading ||
                    scopeLoading ||
                    scopeLoadError ||
                    Boolean(reconnectingExecutionId)
                  }
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
                  <div className="flex min-h-13 items-center gap-2 border-t border-border/60 bg-muted/25 px-2.5 py-2 sm:px-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="hidden items-center gap-1.5 pl-1 text-xs font-medium text-muted-foreground sm:flex">
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
                    </div>
                    <p id="message-help" className="sr-only">
                      {t("workspace.messageHelp")}
                    </p>
                    <Button
                      type="button"
                      className="ml-auto shrink-0 rounded-xl px-3 sm:px-4"
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
                      <span className="hidden sm:inline">{t("workspace.send")}</span>
                    </Button>
                  </div>
                </div>
              </Field>
            </FieldGroup>
          </div>
          <EvidenceViewerDialog
            evidence={citationEvidence}
            loading={citationLoading}
            onClose={() => {
              setCitationEvidence(null);
              setCitationWatermark(null);
            }}
            watermark={citationWatermark}
          />
        </div>
        )}
      </section>
    </>
  );
}

function ConversationScopeSelector({
  items,
  value,
  onValueChange,
  loading,
  disabled,
}: {
  items: DocumentTagSummary[];
  value: DocumentTagSummary[];
  onValueChange: (value: DocumentTagSummary[]) => void;
  loading: boolean;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draftValue, setDraftValue] = useState<DocumentTagSummary[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const normalizedQuery = searchQuery.trim().toLocaleLowerCase();
  const filteredItems = useMemo(
    () => items.filter((item) =>
      !normalizedQuery ||
      item.label.toLocaleLowerCase().includes(normalizedQuery) ||
      item.tag_id.toLocaleLowerCase().includes(normalizedQuery)
    ),
    [items, normalizedQuery],
  );
  const grouped = useMemo(
    () => ({
      team: filteredItems.filter((item) => item.tag_type === "team"),
      project: filteredItems.filter((item) => item.tag_type === "project"),
    }),
    [filteredItems],
  );

  function handleOpenChange(nextOpen: boolean) {
    if (nextOpen) {
      setDraftValue(value);
      setSearchQuery("");
    }
    setOpen(nextOpen);
  }

  function toggleDraftValue(item: DocumentTagSummary, checked: boolean) {
    setDraftValue((current) => {
      const itemKey = scopeTagKey(item);
      if (checked) {
        return current.some((selected) => scopeTagKey(selected) === itemKey)
          ? current
          : [...current, item];
      }
      return current.filter((selected) => scopeTagKey(selected) !== itemKey);
    });
  }

  return (
    <Field>
      <FieldLabel htmlFor="conversation-knowledge-scope-trigger">
        {t("workspace.scope")}
      </FieldLabel>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogTrigger asChild>
          <Button
            id="conversation-knowledge-scope-trigger"
            type="button"
            aria-label={t("workspace.scope")}
            variant="outline"
            className="w-full justify-start"
            disabled={disabled}
          >
            {value.length > 0
              ? t("workspace.scopeSelectedCount", { count: value.length })
              : t("workspace.allAccessible")}
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("workspace.scopeDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("workspace.scopeDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <Field>
            <FieldLabel htmlFor="conversation-knowledge-scope-search">
              {t("workspace.scopeSearch")}
            </FieldLabel>
            <Input
              id="conversation-knowledge-scope-search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder={t("workspace.scopeSearchPlaceholder")}
            />
          </Field>
          <div className="grid max-h-72 gap-4 overflow-y-auto rounded-md border p-2">
            {items.length === 0 ? (
              <Empty className="min-h-32 border-0 p-4 md:p-6">
                <EmptyHeader>
                  <EmptyTitle>{t("workspace.scopeEmpty")}</EmptyTitle>
                </EmptyHeader>
              </Empty>
            ) : filteredItems.length === 0 ? (
              <Empty className="min-h-32 border-0 p-4 md:p-6">
                <EmptyHeader>
                  <EmptyTitle>{t("workspace.scopeNoMatches")}</EmptyTitle>
                </EmptyHeader>
              </Empty>
            ) : (
              (["team", "project"] as const).map((tagType) =>
                grouped[tagType].length > 0 ? (
                  <FieldSet key={tagType} className="gap-2">
                    <FieldLegend variant="label">
                      {t(
                        tagType === "team"
                          ? "workspace.scopeTeam"
                          : "workspace.scopeProject",
                      )}
                    </FieldLegend>
                    {grouped[tagType].map((item) => (
                      <label
                        key={scopeTagKey(item)}
                        className="flex min-h-11 items-center gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:bg-muted/50 focus-within:bg-muted/50 focus-within:outline-none focus-within:ring-2 focus-within:ring-ring"
                      >
                        <Checkbox
                          checked={draftValue.some(
                            (selected) =>
                              scopeTagKey(selected) === scopeTagKey(item),
                          )}
                          onCheckedChange={(checked) =>
                            toggleDraftValue(item, checked === true)
                          }
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium">
                            {item.label}
                          </span>
                          <span className="block truncate text-xs text-muted-foreground">
                            {item.tag_id}
                          </span>
                        </span>
                      </label>
                    ))}
                  </FieldSet>
                ) : null
              )
            )}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
            >
              {t("admin.cancel")}
            </Button>
            <Button
              type="button"
              onClick={() => {
                onValueChange(draftValue);
                setOpen(false);
              }}
            >
              {t("workspace.scopeApply")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <FieldDescription className="flex items-center gap-2">
        {loading && <Spinner aria-hidden="true" />}
        {loading
          ? t("workspace.scopeLoading")
          : value.length > 0
            ? t("workspace.scopeSelectedCount", { count: value.length })
            : t("workspace.allAccessibleDescription")}
      </FieldDescription>
    </Field>
  );
}

function ConversationHistorySidebar({
  className,
  header,
  knowledgeLibraryActive,
  conversations,
  activeConversationId,
  initialLoading,
  loadError,
  loading,
  archivingConversationId,
  onSelect,
  onDelete,
  onNew,
  onOpenKnowledgeLibrary,
  onRetryHistory,
  footer,
}: {
  className?: string;
  header?: ReactNode;
  knowledgeLibraryActive: boolean;
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  initialLoading: boolean;
  loadError: boolean;
  loading: boolean;
  archivingConversationId: string | null;
  onSelect: (conversationId: string) => void;
  onDelete: (conversation: ConversationSummary) => void;
  onNew: () => void;
  onOpenKnowledgeLibrary: () => void;
  onRetryHistory: () => void;
  footer?: ReactNode;
}) {
  const { t } = useTranslation();
  const [deleteCandidate, setDeleteCandidate] = useState<ConversationSummary | null>(null);
  const newConversationActive = !knowledgeLibraryActive && activeConversationId === null;
  return (
    <div className={cn("flex h-full min-h-0 flex-col bg-muted/20", className)}>
      {header}
      <div className="flex flex-col gap-1 p-3 pb-1">
        <Button
          variant={newConversationActive ? "secondary" : "ghost"}
          className="w-full justify-start"
          onClick={onNew}
          aria-current={newConversationActive ? "page" : undefined}
        >
          <MessageSquarePlus data-icon="inline-start" />
          {t("workspace.newConversation")}
        </Button>
        <Button
          variant={knowledgeLibraryActive ? "secondary" : "ghost"}
          className="w-full justify-start"
          onClick={onOpenKnowledgeLibrary}
          aria-current={knowledgeLibraryActive ? "page" : undefined}
        >
          <BookOpen data-icon="inline-start" />
          {t("nav.knowledgeLibrary")}
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
        {initialLoading ? (
          <div
            role="status"
            aria-live="polite"
            aria-busy="true"
            aria-label={t("workspace.historyLoading")}
            className="flex items-center gap-2 text-sm text-muted-foreground"
          >
            <Spinner aria-hidden="true" />
            {t("workspace.historyLoading")}
          </div>
        ) : loadError ? (
          <Alert variant="destructive">
            <AlertTitle>{t("workspace.historyLoadErrorTitle")}</AlertTitle>
            <AlertDescription>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={onRetryHistory}
              >
                {t("workspace.historyRetry")}
              </Button>
            </AlertDescription>
          </Alert>
        ) : conversations.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("workspace.noConversations")}</p>
        ) : null}
        {!initialLoading && conversations.map((conversation) => {
          const active = conversation.conversation_id === activeConversationId;
          const archiving = conversation.conversation_id === archivingConversationId;
          return (
            <div
              key={conversation.conversation_id}
              data-slot="workspace-conversation-item"
              className={cn(
                "group relative rounded-md transition-colors hover:bg-accent/50 focus-within:bg-accent/50",
                active && "bg-secondary hover:bg-secondary focus-within:bg-secondary",
              )}
            >
              <Button
                variant="ghost"
                className={cn(
                  "h-auto min-w-0 w-full justify-start px-3 py-2 text-left hover:bg-transparent",
                  conversation.last_turn_status === "processing" ? "pr-16" : "pr-11",
                )}
                disabled={loading || archiving}
                aria-current={active ? "page" : undefined}
                onClick={() => onSelect(conversation.conversation_id)}
              >
                <span
                  id={`conversation-title-${conversation.conversation_id}`}
                  className="truncate font-medium"
                >
                  {conversation.title}
                </span>
              </Button>
              {conversation.last_turn_status === "processing" && (
                <Spinner
                  className="absolute end-10 top-1/2 size-3 -translate-y-1/2 text-muted-foreground"
                  data-slot="conversation-processing-indicator"
                />
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="absolute end-1 top-1/2 -translate-y-1/2 cursor-pointer opacity-0 group-hover:opacity-70 focus-visible:opacity-100 hover:bg-accent hover:opacity-100 data-[state=open]:bg-accent data-[state=open]:opacity-100"
                    disabled={loading || archiving}
                    aria-label={t("workspace.conversationActions")}
                    aria-describedby={`conversation-title-${conversation.conversation_id}`}
                  >
                    {archiving ? <Spinner aria-hidden="true" /> : <Ellipsis />}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuGroup>
                    <DropdownMenuItem
                      variant="destructive"
                      className="cursor-pointer hover:bg-destructive/10 hover:text-destructive"
                      disabled={conversation.last_turn_status === "processing"}
                      onSelect={() => setDeleteCandidate(conversation)}
                    >
                      <Trash2 data-icon="inline-start" />
                      {t("workspace.deleteConversation")}
                    </DropdownMenuItem>
                  </DropdownMenuGroup>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          );
        })}
      </div>
      {footer && (
        <div
          data-slot="contextual-sidebar-footer"
          className="shrink-0 border-t p-3"
        >
          {footer}
        </div>
      )}
      <AlertDialog
        open={deleteCandidate !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteCandidate(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("workspace.deleteConversationConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("workspace.deleteConversationConfirmDescription", {
                title: deleteCandidate?.title ?? "",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">
              {t("workspace.deleteConversationCancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="cursor-pointer"
              onClick={() => {
                const conversation = deleteCandidate;
                setDeleteCandidate(null);
                if (conversation) onDelete(conversation);
              }}
            >
              {t("workspace.deleteConversationConfirmAction")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ConversationThread({
  turns,
  loading,
  locale,
  onOpenDeclaredEvidence,
  onRetry,
  runtimeProgress,
  liveReasoningTimeline,
  streamingSegments,
}: {
  turns: ConversationTurn[];
  loading: boolean;
  locale: string;
  onOpenDeclaredEvidence: (turnId: string, protectedOpenRef: string) => void;
  onRetry: (turn: ConversationTurn) => void;
  runtimeProgress: string;
  liveReasoningTimeline: ReasoningProgress[];
  streamingSegments: ResponseSegment[];
}) {
  const { t } = useTranslation();
  return (
    <MessageScrollerProvider autoScroll>
      <MessageScroller className="h-full">
        <MessageScrollerViewport>
          <MessageScrollerContent
            className={cn("gap-5 px-4 py-6 md:px-8", turns.length > 0 && "justify-end")}
          >
            {turns.length === 0 && (
              <MessageScrollerItem className="flex min-h-[360px] items-center justify-center">
                <div className="max-w-md text-center">
                  <div className="text-base font-medium">{t("workspace.emptyThread")}</div>
                  <p className="mt-2 text-sm text-muted-foreground">
                    {t("workspace.emptyThreadDescription")}
                  </p>
                </div>
              </MessageScrollerItem>
            )}
            {turns.map((turn, index) => (
              <MessageScrollerItem key={`${turn.turn_id}-${index}`}>
                <Message align={turn.role === "user" ? "end" : "start"}>
                  <MessageContent>
                    <MessageHeader className={turn.role === "user" ? "justify-end" : ""}>
                      <span>{turn.role === "user" ? t("workspace.you") : t("workspace.atlas")}</span>
                      <time
                        dateTime={turn.created_at}
                        className="ml-2 font-normal text-muted-foreground"
                      >
                        {messageTime(turn.created_at, locale)}
                      </time>
                    </MessageHeader>
                    <MessageGroup className={turn.role === "user" ? "w-full items-end" : undefined}>
                      <Bubble
                        align={turn.role === "user" ? "end" : "start"}
                        variant={turn.role === "user" ? "default" : "outline"}
                      >
                        <BubbleContent>
                          {turn.role === "user" ? turn.input_text : (
                            turn.content_state === "access_required" ? (
                              <span>{t("workspace.accessRequired")}</span>
                            ) : turn.execution_status === "processing" ? (
                              <div className="flex flex-col gap-3">
                                {turn.response_segments.length > 0 ? (
                                  <AnswerMarkdown content={answerMarkdownText(turn)} />
                                ) : (
                                  <span className="flex items-center gap-2">
                                    <Spinner />
                                    {processingRuntimePhase(turn, runtimeProgress)
                                      ? t("workspace.runtimeProgress", {
                                          phase: t(`workspace.runtimePhase.${processingRuntimePhase(turn, runtimeProgress)}`),
                                        })
                                      : t("workspace.loadingDescription")}
                                  </span>
                                )}
                                <ReasoningTimeline items={turn.reasoning_timeline} live />
                              </div>
                            ) : turn.response_segments.length > 0 ? (
                              <div className="flex flex-col gap-3">
                                <AnswerMarkdown content={answerMarkdownText(turn)} />
                                <AnswerEvidenceSummary
                                  status={turn.evidence_review_status}
                                  items={turn.model_claimed_evidence}
                                  onOpen={(protectedOpenRef) =>
                                    onOpenDeclaredEvidence(turn.turn_id, protectedOpenRef)}
                                />
                                <ReasoningTimeline items={turn.reasoning_timeline} />
                              </div>
                            ) : turn.answer_text ? (
                              <div className="flex flex-col gap-3">
                                <AnswerMarkdown content={turn.answer_text} />
                                <ReasoningTimeline items={turn.reasoning_timeline} />
                              </div>
                            ) : messageText(turn, t)
                          )}
                        </BubbleContent>
                      </Bubble>
                    </MessageGroup>
                    {turn.role === "assistant" && (
                      turn.execution_status !== "completed" || turn.response_segments.length === 0
                    ) && (
                      <div className="flex flex-col gap-2 px-3">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <StatusBadge {...conversationTurnStatusPresentation(turn, t)} />
                          {turn.execution_status === "failed_closed" && turn.retryable && (
                            <Button variant="outline" size="sm" onClick={() => onRetry(turn)}>{t("workspace.retryQuery")}</Button>
                          )}
                        </div>
                        {turn.response_segments.length === 0 && (
                          <AnswerEvidenceSummary
                            status={turn.evidence_review_status}
                            items={turn.model_claimed_evidence}
                            onOpen={(protectedOpenRef) =>
                              onOpenDeclaredEvidence(turn.turn_id, protectedOpenRef)}
                          />
                        )}
                      </div>
                    )}
                  </MessageContent>
                </Message>
              </MessageScrollerItem>
            ))}
            {loading && !turns.some(
              (turn) => turn.role === "assistant" && turn.execution_status === "processing",
            ) && (
              <MessageScrollerItem>
                <Message>
                  <MessageContent>
                    <MessageHeader>{t("workspace.atlas")}</MessageHeader>
                    <MessageGroup>
                      <Bubble variant="outline">
                        <BubbleContent className="flex flex-col gap-2">
                          {streamingSegments.length > 0 ? (
                            <AnswerMarkdown
                              content={joinResponseSegmentMarkdown(streamingSegments)}
                            />
                          ) : (
                            <div className="flex flex-col gap-3">
                              <span className="flex items-center gap-2"><Spinner />{runtimeProgress ? t("workspace.runtimeProgress", { phase: t(`workspace.runtimePhase.${runtimeProgress}`) }) : t("workspace.loadingDescription")}</span>
                              <ReasoningTimeline items={liveReasoningTimeline} live />
                            </div>
                          )}
                        </BubbleContent>
                      </Bubble>
                    </MessageGroup>
                  </MessageContent>
                </Message>
              </MessageScrollerItem>
            )}
          </MessageScrollerContent>
        </MessageScrollerViewport>
      </MessageScroller>
    </MessageScrollerProvider>
  );
}

function answerMarkdownText(turn: ConversationTurn) {
  return turn.answer_text
    ?? joinResponseSegmentMarkdown(turn.response_segments);
}

function processingRuntimePhase(turn: ConversationTurn, runtimeProgress: string) {
  return turn.reasoning_timeline.at(-1)?.phase ?? runtimeProgress;
}

export function MessageSources({
  citations,
  onOpen,
}: {
  citations: CitationCard[];
  onOpen: (citationId: string) => void;
}) {
  const { t } = useTranslation();
  const uniqueCitations = [...new Map(
    citations.map((citation) => [citation.citation_id, citation]),
  ).values()];
  if (uniqueCitations.length === 0) return null;
  return (
    <div className="flex max-w-2xl flex-col gap-2">
      <div className="text-xs font-medium text-muted-foreground">
        {t("workspace.responseSources")}
      </div>
      {uniqueCitations.map((citation) => (
        <button
          key={citation.citation_id}
          type="button"
          className="rounded-md border bg-muted/30 p-3 text-left transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
          disabled={!citation.viewer_available}
          onClick={() => onOpen(citation.citation_id)}
          aria-label={t("citationViewer.openCitation", { title: citation.document_title })}
        >
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{citation.document_title}</div>
              <div className="text-xs text-muted-foreground">
                {(citation.document_format ?? "document").toUpperCase()} · {citation.locator_label}
                {citation.evidence_modality === "visual_inference" ? ` · ${t("workspace.visualInference")}` : ""}
              </div>
            </div>
            <Badge variant="outline">
              {citation.viewer_available
                ? t("citationViewer.open")
                : t("citationViewer.unavailable")}
            </Badge>
          </div>
          <p className="mt-2 text-sm leading-6">{citation.snippet}</p>
          {!citation.viewer_available && (
            <p className="mt-2 text-xs text-muted-foreground">
              {t("citationViewer.previewUnavailable")}
            </p>
          )}
        </button>
      ))}
    </div>
  );
}

function messageText(turn: ConversationTurn, t: Parameters<typeof serverMessage>[1]) {
  if (turn.content_state === "access_required") return serverMessage(turn.user_reason, t);
  if (turn.answer_text) return turn.answer_text;
  if (turn.response_kind === "unknown") return serverMessage(turn.user_reason, t);
  if (turn.execution_status === "failed_closed") return serverMessage(turn.user_reason, t);
  if (turn.response_kind === "refused") return serverMessage(turn.user_reason, t);
  return t("workspace.pendingAnswer");
}

function segmentVerificationBadge(
  segment: ResponseSegment,
  t: Parameters<typeof serverMessage>[1],
) {
  if (segment.verification_status === "evidence_supported") {
    return <StatusBadge semantic="success" label={t("workspace.claimEvidenceSupported")} className="w-fit" />;
  }
  if (segment.verification_status === "unverified_inference") {
    return <StatusBadge semantic="unknown" label={t("workspace.claimUnverified")} className="w-fit" />;
  }
  if (segment.verification_status === "conflict") {
    return <StatusBadge semantic="failure" label={t("workspace.claimConflict")} className="w-fit" />;
  }
  if (segment.verification_status === "mixed") {
    return <StatusBadge semantic="unknown" label={t("status.mixedAnswer")} className="w-fit" />;
  }
  if (segment.external_unverified) {
    return <StatusBadge semantic="unknown" label={t("workspace.externalUnverified")} className="w-fit" />;
  }
  if (
    segment.kind === "dialogue" &&
    segment.verification_status === "not_applicable"
  ) {
    return <StatusBadge semantic="inactive" label={t("workspace.dialogueNoEvidence")} className="w-fit" />;
  }
  return null;
}

function claimVerificationBadge(
  claim: ResponseSegment["claims"][number],
  t: Parameters<typeof serverMessage>[1],
) {
  if (claim.verification_status === "evidence_supported") {
    return <StatusBadge semantic="success" label={t("workspace.claimEvidenceSupported")} className="w-fit" />;
  }
  if (claim.verification_status === "conflict") {
    return <StatusBadge semantic="failure" label={t("workspace.claimConflict")} className="w-fit" />;
  }
  return <StatusBadge semantic="unknown" label={t("workspace.claimUnverified")} className="w-fit" />;
}

function annotatedSegmentText(segment: ResponseSegment): ReactNode[] {
  const claims = claimsInPresentationOrder(segment);
  const nodes: ReactNode[] = [];
  let cursor = 0;
  claims.forEach((claim, index) => {
    if (cursor < claim.start) nodes.push(sliceCodePoints(segment.text, cursor, claim.start));
    const statusClass = claim.verification_status === "evidence_supported"
      ? "decoration-success"
      : claim.verification_status === "conflict"
        ? "decoration-destructive"
        : "decoration-muted-foreground";
    nodes.push(
      <span
        key={claim.claim_id}
        className={cn("underline decoration-2 underline-offset-4", statusClass)}
        data-claim-id={claim.claim_id}
      >
        {sliceCodePoints(segment.text, claim.start, claim.end)}
        <sup className="ml-0.5 text-[0.65em] text-muted-foreground">{index + 1}</sup>
      </span>,
    );
    cursor = claim.end;
  });
  const codePointLength = Array.from(segment.text).length;
  if (cursor < codePointLength) nodes.push(sliceCodePoints(segment.text, cursor));
  return nodes;
}

export function sliceCodePoints(value: string, start: number, end?: number): string {
  return Array.from(value).slice(start, end).join("");
}

export function claimsInPresentationOrder(
  segment: ResponseSegment,
): ResponseSegment["claims"] {
  return [...segment.claims].sort(
    (left, right) => left.start - right.start || left.end - right.end,
  );
}

function resultToTurn(result: ConversationTurnResult): ConversationTurn {
  return {
    ...result,
    role: "assistant",
    input_text: null,
  };
}

function captureRuntimeProgress(
  event: RuntimeStreamEvent,
  eventType: string,
  setRuntimeProgress: Dispatch<SetStateAction<string>>,
  setTimeline: Dispatch<SetStateAction<ReasoningProgress[]>>,
): ReasoningProgress | null {
  if (event.phase) setRuntimeProgress(event.phase);
  if (
    eventType !== "reasoning_progressed" ||
    !event.event_id ||
    !event.reasoning_phase ||
    !event.progress_status ||
    !event.created_at ||
    !event.message_code
  ) return null;
  const progress: ReasoningProgress = {
    event_id: event.event_id,
    sequence: event.sequence,
    phase: event.reasoning_phase,
    status: event.progress_status,
    cycle: event.cycle ?? null,
    message_code: event.message_code,
    message_params: event.message_params ?? {},
    created_at: event.created_at,
  };
  setTimeline((current) => mergeReasoningProgress(current, progress));
  return progress;
}

function mergeReasoningProgress(
  current: ReasoningProgress[],
  progress: ReasoningProgress,
) {
  return [
    ...current.filter((item) => item.event_id !== progress.event_id),
    progress,
  ].sort((left, right) => left.sequence - right.sequence);
}

function mergeStreamingSegment(
  turn: ConversationTurn,
  segment: ResponseSegment,
): ConversationTurn {
  const replacesExisting = turn.response_segments.some(
    (current) => current.segment_id === segment.segment_id,
  );
  const responseSegments = replacesExisting
    ? turn.response_segments.map((current) =>
        current.segment_id === segment.segment_id ? segment : current,
      )
    : [...turn.response_segments, segment];
  return {
    ...turn,
    answer_text: joinResponseSegmentMarkdown(responseSegments),
    response_segments: responseSegments,
  };
}

function createIdempotencyKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function messageTime(value: string, locale: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
