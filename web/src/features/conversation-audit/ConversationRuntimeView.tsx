import { useTranslation } from "react-i18next";
import { Badge } from "../../components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardFooter,
  CardTitle,
} from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "../../components/ui/table";
import {
  LoadErrorState,
  LoadingState,
  StatusBadge,
  conversationTurnStatusPresentation,
} from "../../shared/product-ui";
import type {
  ConversationTurn,
} from "../workspace/index";
import {
  AuditField,
  AuditTraceValue,
  formatDateTime,
  type DiscoveryPreview,
} from "./AuditPresentationUtils";
import type { ReasoningTrace, RuntimeTraceDetail } from "./types";

export function ConversationRuntimeView({
  conversationLoading,
  conversationError,
  selectedConversationId,
  selectedRuntimeTurn,
  runtimeLoading,
  runtimeError,
  selectedRuntime,
  locale,
  onOpenConversation,
  onOpenRuntime,
  onDiscoveryPreviewChange,
}: {
  conversationLoading: boolean;
  conversationError: string;
  selectedConversationId: string | null;
  selectedRuntimeTurn: ConversationTurn | null;
  runtimeLoading: boolean;
  runtimeError: string;
  selectedRuntime: RuntimeTraceDetail | null;
  locale: string;
  onOpenConversation: (conversationId: string) => Promise<void>;
  onOpenRuntime: (turn: ConversationTurn) => Promise<void>;
  onDiscoveryPreviewChange: (preview: DiscoveryPreview | null) => void;
}) {
  const { t } = useTranslation();
  const displayedAuditSteps = selectedRuntime?.audit_steps.filter(
    (step) => step.step_kind === "model" || step.step_kind === "tool",
  ) ?? [];
  return (
<div className="min-w-0 max-h-[min(42rem,70vh)] overflow-y-auto">
                  {selectedRuntimeTurn && (
                    <div className="mb-3 flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground">
                      <time dateTime={selectedRuntimeTurn.created_at}>
                        {t("audit.runtimeTurn", {
                          value: formatDateTime(
                            selectedRuntimeTurn.created_at,
                            locale,
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
                        onRetry={() => void onOpenConversation(selectedConversationId)}
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
                        onRetry={() => void onOpenRuntime(selectedRuntimeTurn)}
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
                                locale,
                              )}
                            />
                            <AuditField
                              label={t("audit.updatedAtLabel")}
                              value={formatDateTime(
                                selectedRuntime.updated_at,
                                locale,
                              )}
                            />
                          </div>
                        </div>
                        <PromptSkillCatalogPanel
                          catalogs={selectedRuntime.prompt_skill_catalogs}
                        />
                        <ExecutionSkillSelectionPanel
                          selections={selectedRuntime.prompt_skill_selections}
                          trace={selectedRuntime.reasoning_trace}
                        />
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
                        <section
                          data-slot="audit-step-activity"
                          aria-labelledby="audit-step-activity-title"
                        >
                          <div id="audit-step-activity-title" className="font-medium">
                            {t("audit.modelToolActivity")}
                          </div>
                          <p className="mt-1 text-sm text-muted-foreground">
                            {t("audit.modelToolActivityDescription")}
                          </p>
                          {displayedAuditSteps.length === 0 ? (
                            <p className="mt-2 text-sm text-muted-foreground">
                              {t("audit.noModelToolActivity")}
                            </p>
                          ) : (
                            <Table className="mt-2">
                              <TableHeader>
                                <TableRow>
                                  <TableHead>{t("audit.stepOrdinal")}</TableHead>
                                  <TableHead>{t("audit.stepType")}</TableHead>
                                  <TableHead>{t("audit.stepOperation")}</TableHead>
                                  <TableHead>{t("audit.stepStatus")}</TableHead>
                                  <TableHead>{t("audit.stepResultRef")}</TableHead>
                                  <TableHead>{t("audit.stepUsage")}</TableHead>
                                </TableRow>
                              </TableHeader>
                              <TableBody>
                                {displayedAuditSteps.map((step) => (
                                  <TableRow
                                    key={`${step.ordinal}:${step.step_kind}:${step.operation}`}
                                  >
                                    <TableCell>{step.ordinal}</TableCell>
                                    <TableCell>
                                      {t(
                                        step.step_kind === "model"
                                          ? "audit.modelDecision"
                                          : "audit.toolUse",
                                      )}
                                    </TableCell>
                                    <TableCell className="font-mono text-xs">
                                      {step.operation}
                                    </TableCell>
                                    <TableCell>
                                      <Badge variant="outline">{step.status}</Badge>
                                    </TableCell>
                                    <TableCell className="font-mono text-xs">
                                      {step.result_ref ?? "—"}
                                    </TableCell>
                                    <TableCell>
                                      {step.step_kind === "model"
                                        ? t("audit.modelStepUsage", {
                                            input: step.input_tokens,
                                            output: step.output_tokens,
                                          })
                                        : t("audit.toolStepUsage", {
                                            output: step.output_tokens,
                                            evidence: step.evidence_count,
                                          })}
                                    </TableCell>
                                  </TableRow>
                                ))}
                              </TableBody>
                            </Table>
                          )}
                        </section>
                        <div
                          data-slot="model-visible-item-diagnostic"
                          className={
                            selectedRuntime.model_visible_item_exceeded
                              ? "rounded-md border border-destructive/40 bg-destructive/5 p-4"
                              : "rounded-md border p-4"
                          }
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="font-medium">{t("audit.modelVisibleItems")}</div>
                            <Badge
                              variant={selectedRuntime.model_visible_item_exceeded ? "destructive" : "outline"}
                            >
                              {t(
                                selectedRuntime.model_visible_item_exceeded
                                  ? "audit.modelVisibleItemsExceeded"
                                  : "audit.modelVisibleItemsWithinLimit",
                              )}
                            </Badge>
                          </div>
                          <p className="mt-1 text-sm text-muted-foreground">
                            {t("audit.modelVisibleItemsDescription")}
                          </p>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            <AuditField
                              label={t("audit.modelVisibleItemsCount")}
                              value={String(selectedRuntime.model_visible_item_count)}
                            />
                            <AuditField
                              label={t("audit.modelVisibleItemsLimit")}
                              value={String(selectedRuntime.model_visible_item_limit)}
                            />
                          </div>
                        </div>
                        <div>
                          <div className="font-medium">{t("audit.runtimeBudget")}</div>
                          <Table className="mt-2">
                            <TableHeader>
                              <TableRow>
                                <TableHead>{t("audit.providerCalls")}</TableHead>
                                <TableHead>{t("audit.toolCalls")}</TableHead>
                                <TableHead>{t("audit.searchRounds")}</TableHead>
                                <TableHead>{t("audit.modelVisibleItemsCount")}</TableHead>
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
                                <TableCell>{selectedRuntime.budget.model_visible_items}</TableCell>
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
                                                    onClick={() => onDiscoveryPreviewChange(candidate)}
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
                                  <TableCell>{formatDateTime(event.created_at, locale)}</TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      </>
                    )}
                  </div>
                </div>
  );
}

function PromptSkillCatalogPanel({
  catalogs,
}: {
  catalogs: RuntimeTraceDetail["prompt_skill_catalogs"];
}) {
  const { t } = useTranslation();
  return (
    <section
      className="flex flex-col gap-3"
      aria-labelledby="prompt-skill-catalogs-title"
    >
      <div>
        <div id="prompt-skill-catalogs-title" className="font-medium">
          {t("audit.promptSkillCatalogs")}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("audit.promptSkillCatalogsDescription")}
        </p>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {catalogs.map((catalog) => (
          <Card key={catalog.category}>
            <CardHeader>
              <CardTitle>{catalog.category}</CardTitle>
              <CardDescription>
                {t("audit.promptSkillCatalogRevisionValue", {
                  revision: catalog.catalog_revision,
                })}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <AuditTraceValue
                label={t("audit.promptSkillCatalogDigest")}
                value={catalog.catalog_digest}
              />
            </CardContent>
            <CardFooter>
              <Badge variant="outline">
                {t("audit.promptSkillCatalogPinned")}
              </Badge>
            </CardFooter>
          </Card>
        ))}
      </div>
    </section>
  );
}

function ExecutionSkillSelectionPanel({
  selections,
  trace,
}: {
  selections: RuntimeTraceDetail["prompt_skill_selections"];
  trace: ReasoningTrace | null;
}) {
  const { t } = useTranslation();
  return (
    <section
      className="flex flex-col gap-3"
      aria-labelledby="execution-skill-selections-title"
    >
      <div>
        <div id="execution-skill-selections-title" className="font-medium">
          {t("audit.executionPromptSkillSelections")}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("audit.executionPromptSkillSelectionsDescription")}
        </p>
      </div>
      <div className="flex flex-col gap-3">
        {selections.map((selection) => {
          const gate =
            selection.node === "answer_candidate"
              ? trace?.provisional_evidence_checks.find(
                  (check) => check.ordinal === selection.candidate_ordinal,
                )
              : null;
          const identity =
            selection.node === "resolver"
              ? t("audit.promptSkillResolver")
              : t("audit.promptSkillAnswerCandidate", {
                  ordinal: selection.candidate_ordinal,
                  kind: selection.candidate_kind,
                });
          return (
            <Card
              key={`${selection.node}:${selection.candidate_ordinal ?? 0}`}
            >
              <CardHeader>
                <CardTitle>{identity}</CardTitle>
                <CardDescription>{selection.category}</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-2 sm:grid-cols-2">
                <AuditField
                  label={t("audit.promptSkillSelectionStatus")}
                  value={selection.status}
                />
                <AuditField
                  label={t("audit.promptSkillFallbackCode")}
                  value={selection.fallback_code ?? "—"}
                />
                {selection.node === "answer_candidate" ? (
                  <AuditField
                    label={t("audit.promptSkillLinkedGate")}
                    value={
                      gate
                        ? `${gate.ordinal} · ${gate.consistency} · ${gate.candidate_disposition}`
                        : "—"
                    }
                  />
                ) : null}
              </CardContent>
              <CardFooter className="flex flex-col items-start gap-1">
                {selection.selected_skills.length > 0 ? (
                  selection.selected_skills.map((skill) => (
                    <AuditTraceValue
                      key={`${skill.name}:${skill.revision}:${skill.content_digest}`}
                      label={t("audit.promptSkillSelectedRef")}
                      value={`${skill.name} · r${skill.revision} · ${skill.content_digest}`}
                    />
                  ))
                ) : (
                  <span className="text-xs text-muted-foreground">
                    {t("audit.promptSkillBaseline")}
                  </span>
                )}
              </CardFooter>
            </Card>
          );
        })}
      </div>
    </section>
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
        <div className="text-sm font-medium">{t("audit.promptSkillSelections")}</div>
        {trace.skill_selections.length === 0 ? (
          <p className="mt-1 text-sm text-muted-foreground">{t("audit.notReported")}</p>
        ) : (
          <div className="mt-2 flex flex-col gap-2">
            {trace.skill_selections.map((selection) => (
              <div
                key={`${selection.node}-${selection.plan_generation}`}
                className="rounded-md border p-3"
              >
                <div className="grid gap-2 sm:grid-cols-2">
                  <AuditField label={t("audit.promptSkillSelectionNode")} value={selection.node} />
                  <AuditField
                    label={t("audit.promptSkillPlanGeneration")}
                    value={String(selection.plan_generation)}
                  />
                  <AuditField label={t("audit.promptSkillSelectionStatus")} value={selection.status} />
                  <AuditField
                    label={t("audit.promptSkillFallbackCode")}
                    value={selection.fallback_code ?? "—"}
                  />
                </div>
                {selection.selected_skills.length > 0 ? (
                  <ul className="mt-2 flex flex-col gap-1 text-xs">
                    {selection.selected_skills.map((skill) => (
                      <li key={`${skill.name}-${skill.revision}-${skill.content_digest}`}>
                        {t("audit.promptSkillReference", {
                          name: skill.name,
                          revision: skill.revision,
                          digest: skill.content_digest,
                        })}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ))}
          </div>
        )}
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
