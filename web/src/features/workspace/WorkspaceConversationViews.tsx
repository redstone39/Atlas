import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "../../components/ui/badge";
import { Bubble, BubbleContent } from "../../components/ui/bubble";
import { Button } from "../../components/ui/button";
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
import { Spinner } from "../../components/ui/spinner";
import {
  ToggleGroup,
  ToggleGroupItem,
} from "../../components/ui/toggle-group";
import {
  StatusBadge,
  conversationTurnStatusPresentation,
  serverMessage,
} from "../../shared/product-ui";
import { cn } from "../../lib/utils";
import { AnswerEvidenceSummary } from "./AnswerEvidenceSummary";
import { AnswerMarkdown } from "./AnswerMarkdown";
import { joinResponseSegmentMarkdown } from "./api";
import { ReasoningTimeline } from "./ReasoningTimeline";
import type {
  CitationCard,
  ConversationTurn,
  ReasoningProgress,
  ResponseSegment,
  TurnFeedbackValue,
} from "./types";
import {
  answerMarkdownText,
  messageTime,
  processingRuntimePhase,
} from "./workspaceProjections";

export function ConversationThread({
  turns,
  loading,
  locale,
  onOpenDeclaredEvidence,
  onRetry,
  onFeedbackChange,
  pendingFeedbackTurnIds,
  runtimeProgress,
  liveReasoningTimeline,
  streamingSegments,
}: {
  turns: ConversationTurn[];
  loading: boolean;
  locale: string;
  onOpenDeclaredEvidence: (turnId: string, protectedOpenRef: string) => void;
  onRetry: (turn: ConversationTurn) => void;
  onFeedbackChange: (
    turn: ConversationTurn,
    feedback: TurnFeedbackValue,
  ) => void;
  pendingFeedbackTurnIds: ReadonlySet<string>;
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
            className={cn("gap-5 px-4 py-4 md:px-8", turns.length > 0 && "justify-end")}
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
                      <time dateTime={turn.created_at} className="ml-2 font-normal text-muted-foreground">
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
                    {turn.role === "assistant" &&
                      turn.execution_status === "completed" &&
                      answerMarkdownText(turn).trim().length > 0 && (
                        <div className="flex flex-col gap-2 px-3">
                          <p className="text-sm font-medium">
                            {t("workspace.feedbackPrompt")}
                          </p>
                          <ToggleGroup
                            type="single"
                            variant="outline"
                            size="sm"
                            value={turn.feedback?.feedback ?? ""}
                            aria-label={t("workspace.feedbackPrompt")}
                            onValueChange={(value) => {
                              if (value === "helpful" || value === "not_helpful") {
                                onFeedbackChange(turn, value);
                              }
                            }}
                          >
                            <ToggleGroupItem
                              value="helpful"
                              disabled={pendingFeedbackTurnIds.has(turn.turn_id)}
                              aria-label={t("workspace.feedbackHelpful")}
                            >
                              <ThumbsUp data-icon="inline-start" />
                              {t("workspace.feedbackHelpful")}
                            </ToggleGroupItem>
                            <ToggleGroupItem
                              value="not_helpful"
                              disabled={pendingFeedbackTurnIds.has(turn.turn_id)}
                              aria-label={t("workspace.feedbackNotHelpful")}
                            >
                              <ThumbsDown data-icon="inline-start" />
                              {t("workspace.feedbackNotHelpful")}
                            </ToggleGroupItem>
                          </ToggleGroup>
                        </div>
                      )}
                    {turn.role === "assistant" && (
                      turn.execution_status !== "completed" || turn.response_segments.length === 0
                    ) && (
                      <div className="flex flex-col gap-2 px-3">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <StatusBadge {...conversationTurnStatusPresentation(turn, t)} />
                          {turn.execution_status === "failed_closed" && turn.retryable && (
                            <Button variant="outline" size="sm" onClick={() => onRetry(turn)}>
                              {t("workspace.retryQuery")}
                            </Button>
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
                            <AnswerMarkdown content={joinResponseSegmentMarkdown(streamingSegments)} />
                          ) : (
                            <div className="flex flex-col gap-3">
                              <span className="flex items-center gap-2">
                                <Spinner />
                                {runtimeProgress
                                  ? t("workspace.runtimeProgress", { phase: t(`workspace.runtimePhase.${runtimeProgress}`) })
                                  : t("workspace.loadingDescription")}
                              </span>
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
              {citation.viewer_available ? t("citationViewer.open") : t("citationViewer.unavailable")}
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

function messageText(
  turn: ConversationTurn,
  t: Parameters<typeof serverMessage>[1],
) {
  if (turn.content_state === "access_required") return serverMessage(turn.user_reason, t);
  if (turn.answer_text) return turn.answer_text;
  if (turn.response_kind === "unknown") return serverMessage(turn.user_reason, t);
  if (turn.execution_status === "failed_closed") return serverMessage(turn.user_reason, t);
  if (turn.response_kind === "refused") return serverMessage(turn.user_reason, t);
  return t("workspace.pendingAnswer");
}
