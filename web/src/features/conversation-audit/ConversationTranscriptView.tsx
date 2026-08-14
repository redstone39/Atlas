import { useTranslation } from "react-i18next";
import { Button } from "../../components/ui/button";
import { Bubble, BubbleContent } from "../../components/ui/bubble";
import { Message, MessageContent, MessageHeader } from "../../components/ui/message";
import {
  LoadErrorState,
  LoadingState,
  StatusBadge,
  TechnicalDetails,
  conversationTurnStatusPresentation,
  serverMessage,
} from "../../shared/product-ui";
import {
  adminAuditConversationRoute,
  type AppRoute,
} from "../../shared/routes";
import type {
  ConversationDetail,
  ConversationTurn,
} from "../workspace/index";
import {
  AnswerMarkdown,
  ClaimedEvidenceTrace,
} from "../workspace/index";
import {
  assistantAttemptPosition,
  AuditTraceValue,
} from "./AuditPresentationUtils";

export function ConversationTranscriptView({
  conversationLoading,
  conversationError,
  selectedConversationId,
  selectedConversation,
  onNavigate,
  onOpenConversation,
  onOpenDeclaredEvidence,
}: {
  conversationLoading: boolean;
  conversationError: string;
  selectedConversationId: string | null;
  selectedConversation: ConversationDetail | null;
  onNavigate: (route: AppRoute) => void;
  onOpenConversation: (conversationId: string) => Promise<void>;
  onOpenDeclaredEvidence: (
    turn: ConversationTurn,
    protectedOpenRef: string,
  ) => Promise<void>;
}) {
  const { t } = useTranslation();
  return (
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
                      onRetry={() => void onOpenConversation(selectedConversationId)}
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
                                    {turn.answer_text ? (
                                      <AnswerMarkdown content={turn.answer_text} />
                                    ) : (
                                      <span>
                                        {turn.input_text ?? serverMessage(turn.user_reason, t)}
                                      </span>
                                    )}
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
                                          void onOpenDeclaredEvidence(turn, protectedOpenRef)}
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
  );
}
