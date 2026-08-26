import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "../../components/ui/empty";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { AdminBreadcrumb } from "../../shared/admin-detail";
import { PageHeader, StatusBadge } from "../../shared/product-ui";
import { adminAgentResearchAuditRoute, type AppRoute } from "../../shared/routes";
import type {
  AgentResearchAuditDetail,
  AgentResearchAuditListItem,
  AgentResearchRuntimeDetail,
  ResearchEvidenceDescriptor,
} from "./types";

export function AuditShell({
  section,
  onNavigate,
  children,
}: {
  section: "list" | "detail" | "runtime";
  onNavigate: (route: AppRoute) => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className="flex flex-col gap-5">
      <AdminBreadcrumb
        onNavigate={onNavigate}
        items={[
          { label: t("audit.title"), route: "/admin/audit" },
          {
            label: t("agentResearchAudit.title"),
            route: section === "list" ? undefined : adminAgentResearchAuditRoute(),
          },
          ...(section === "runtime" ? [{ label: t("audit.runtime") }] : []),
        ]}
      />
      <PageHeader
        title={t("agentResearchAudit.title")}
        description={t("agentResearchAudit.description")}
      />
      {children}
    </section>
  );
}

export function ResearchDirectory({
  items,
  nextCursor,
  loadingMore,
  onLoadMore,
  onOpen,
}: {
  items: AgentResearchAuditListItem[];
  nextCursor: string | null;
  loadingMore: boolean;
  onLoadMore: () => void;
  onOpen: (researchId: string) => void;
}) {
  const { t } = useTranslation();
  if (items.length === 0) {
    return (
      <Empty className="border">
        <EmptyHeader>
          <EmptyTitle>{t("agentResearchAudit.emptyTitle")}</EmptyTitle>
          <EmptyDescription>{t("agentResearchAudit.emptyDescription")}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="hidden overflow-hidden rounded-lg border md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("agentResearchAudit.status")}</TableHead>
              <TableHead>{t("agentResearchAudit.actor")}</TableHead>
              <TableHead>{t("audit.time")}</TableHead>
              <TableHead className="text-right">{t("agents.action")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.kind === "accepted" ? item.research_id : item.event_id}>
                <TableCell>
                  <StatusBadge
                    semantic={item.kind === "denied" ? "denied" : item.status === "completed" ? "success" : "progress"}
                    label={item.kind === "denied" ? t("agentResearchAudit.denied") : t(`agentResearchAudit.${item.status}`)}
                  />
                </TableCell>
                <TableCell>{item.actor_id ?? t("agentResearchAudit.unknownActor")}</TableCell>
                <TableCell><time dateTime={item.occurred_at}>{formatDate(item.occurred_at)}</time></TableCell>
                <TableCell className="text-right">
                  {item.kind === "accepted" ? (
                    <Button size="sm" variant="outline" onClick={() => onOpen(item.research_id)}>
                      {t("agentResearchAudit.open")}
                    </Button>
                  ) : (
                    <span className="text-sm text-muted-foreground">{item.reason}</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="grid gap-3 md:hidden">
        {items.map((item) => (
          <Card key={item.kind === "accepted" ? item.research_id : item.event_id}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between gap-3 text-base">
                <span>{item.actor_id ?? t("agentResearchAudit.unknownActor")}</span>
                <StatusBadge
                  semantic={item.kind === "denied" ? "denied" : item.status === "completed" ? "success" : "progress"}
                  label={item.kind === "denied" ? t("agentResearchAudit.denied") : t(`agentResearchAudit.${item.status}`)}
                />
              </CardTitle>
            </CardHeader>
            <CardContent className="flex items-center justify-between gap-3">
              <time className="text-sm text-muted-foreground" dateTime={item.occurred_at}>{formatDate(item.occurred_at)}</time>
              {item.kind === "accepted" ? (
                <Button size="sm" onClick={() => onOpen(item.research_id)}>{t("agentResearchAudit.open")}</Button>
              ) : (
                <span className="text-sm text-muted-foreground">{item.reason}</span>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
      {nextCursor && (
        <Button className="self-start" variant="outline" disabled={loadingMore} onClick={onLoadMore}>
          {loadingMore ? t("agentResearchAudit.loadingMore") : t("agentResearchAudit.loadMore")}
        </Button>
      )}
    </div>
  );
}

export function ResearchDetail({
  detail,
  onOpenRuntime,
  onOpenEvidence,
}: {
  detail: AgentResearchAuditDetail;
  onOpenRuntime: () => void;
  onOpenEvidence: (
    descriptor: ResearchEvidenceDescriptor,
    representation: "text" | "visual" | "native",
  ) => void;
}) {
  const { t } = useTranslation();
  const evidenceById = new Map(
    detail.packet?.evidence.map((item) => [item.evidence_id, item]) ?? [],
  );
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center justify-between gap-3">
            <span>{detail.question}</span>
            <StatusBadge
              semantic={detail.status === "completed" ? "success" : "progress"}
              label={t(`agentResearchAudit.${detail.status}`)}
            />
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
          <KeyValue label={t("agentResearchAudit.actor")} value={detail.actor_id} />
          <KeyValue
            label={t("agentResearchAudit.executionId")}
            value={detail.execution_id}
          />
          <KeyValue
            label={t("agentResearchAudit.outputMode")}
            value={detail.output_mode}
          />
          <KeyValue
            label={t("agentResearchAudit.projects")}
            value={detail.accepted_scope.project_ids.join(", ")}
          />
          {detail.packet && (
            <KeyValue
              label={t("agentResearchAudit.packetDigest")}
              value={detail.packet.packet_digest}
            />
          )}
          <Button
            className="w-fit sm:col-span-2"
            variant="outline"
            onClick={onOpenRuntime}
          >
            {t("audit.viewRuntimeTrace")}
          </Button>
        </CardContent>
      </Card>
      {detail.answer && (
        <Card>
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center justify-between gap-3">
              <span>{t("agentResearchAudit.answer")}</span>
              <StatusBadge
                semantic={
                  detail.answer.status === "available"
                    ? "success"
                    : detail.answer.status === "unavailable"
                      ? "attention"
                      : "inactive"
                }
                label={t(`agentResearchAudit.answerStatus.${detail.answer.status}`)}
              />
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 text-sm sm:grid-cols-2">
              <KeyValue
                label={t("agentResearchAudit.answerPacketRef")}
                value={detail.answer.packet_ref}
              />
              <KeyValue
                label={t("agentResearchAudit.answerPacketDigest")}
                value={detail.answer.packet_digest}
              />
            </div>
            {detail.answer.status === "available" &&
              detail.answer.governed_answer && (
                <div className="space-y-3 whitespace-pre-wrap text-sm leading-6">
                  {detail.answer.governed_answer.segments.map((segment) => (
                    <p key={segment.segment_id}>{segment.text}</p>
                  ))}
                </div>
              )}
          </CardContent>
        </Card>
      )}
      {detail.packet && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>{t("agentResearchAudit.findings")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {detail.packet.findings.map((finding) => (
                <article key={finding.finding_id} className="rounded-md border p-3">
                  <div className="mb-2">
                    <StatusBadge
                      semantic={
                        finding.evidence_assessment === "conflict"
                          ? "attention"
                          : finding.evidence_assessment === "insufficient"
                            ? "inactive"
                            : "success"
                      }
                      label={finding.evidence_assessment}
                    />
                  </div>
                  <p className="text-sm leading-6">{finding.text}</p>
                  <div className="mt-3 text-sm">
                    <div className="text-xs font-medium text-muted-foreground">
                      {t("agentResearchAudit.evidenceMapping")}
                    </div>
                    {finding.evidence_ids.length === 0 ? (
                      <p className="mt-1 text-muted-foreground">
                        {t("agentResearchAudit.noMappedEvidence")}
                      </p>
                    ) : (
                      <ul className="mt-1 list-inside list-disc space-y-1">
                        {finding.evidence_ids.map((evidenceId) => {
                          const descriptor = evidenceById.get(evidenceId);
                          return (
                            <li key={evidenceId}>
                              {descriptor
                                ? `${descriptor.title} · ${descriptor.locator}`
                                : evidenceId}
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                </article>
              ))}
            </CardContent>
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>{t("agentResearchAudit.unresolvedQuestions")}</CardTitle>
              </CardHeader>
              <CardContent>
                {detail.packet.unresolved_questions.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("agentResearchAudit.noUnresolvedQuestions")}
                  </p>
                ) : (
                  <ul className="list-inside list-disc space-y-2 text-sm">
                    {detail.packet.unresolved_questions.map((question) => (
                      <li key={question}>{question}</li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>{t("agentResearchAudit.researchLimits")}</CardTitle>
              </CardHeader>
              <CardContent>
                {detail.packet.research_limits.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("agentResearchAudit.noResearchLimits")}
                  </p>
                ) : (
                  <div className="space-y-3">
                    {detail.packet.research_limits.map((limit) => (
                      <div key={`${limit.code}:${limit.detail}`}>
                        <div className="text-sm font-medium">{limit.code}</div>
                        <p className="text-sm text-muted-foreground">{limit.detail}</p>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
          <Card>
            <CardHeader>
              <CardTitle>{t("agentResearchAudit.evidence")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 lg:grid-cols-2">
              {detail.packet.evidence.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("agentResearchAudit.noEvidence")}
                </p>
              ) : detail.packet.evidence.map((descriptor) => (
                <div
                  key={descriptor.evidence_id}
                  className="flex flex-col gap-3 rounded-md border p-3"
                >
                  <div>
                    <p className="font-medium">{descriptor.title}</p>
                    <p className="text-sm text-muted-foreground">
                      {descriptor.locator}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {descriptor.available_representations.map((representation) => (
                      <Button
                        key={representation}
                        size="sm"
                        variant="outline"
                        onClick={() => onOpenEvidence(descriptor, representation)}
                      >
                        {t(`agentResearchAudit.representation.${representation}`)}
                      </Button>
                    ))}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </>
      )}
      <Card>
        <CardHeader>
          <CardTitle>{t("agentResearchAudit.businessEvents")}</CardTitle>
        </CardHeader>
        <CardContent>
          {detail.business_events.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("agentResearchAudit.noBusinessEvents")}
            </p>
          ) : (
            <div className="space-y-2">
              {detail.business_events.map((event) => (
                <div
                  key={event.event_id}
                  className="grid gap-1 rounded-md border p-3 text-sm sm:grid-cols-[1fr_1fr_auto]"
                >
                  <span>{event.event_type}</span>
                  <span className="text-muted-foreground">{event.message_code}</span>
                  <time
                    className="text-muted-foreground"
                    dateTime={event.created_at}
                  >
                    {formatDate(event.created_at)}
                  </time>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export function ResearchRuntime({ runtime }: { runtime: AgentResearchRuntimeDetail }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center justify-between gap-3">
            <span>{t("agentResearchAudit.runtimeTitle")}</span>
            <StatusBadge semantic={runtime.failure_code ? "failure" : runtime.state.includes("completed") ? "success" : "progress"} label={runtime.state} />
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <KeyValue label={t("agentResearchAudit.executionId")} value={runtime.execution_id} />
          <KeyValue label={t("audit.reasoningMode")} value={runtime.reasoning_mode} />
          <KeyValue label={t("agentResearchAudit.toolInvocations")} value={String(runtime.budget.tool_invocations)} />
          <KeyValue label={t("agentResearchAudit.providerInvocations")} value={String(runtime.budget.provider_invocations)} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>{t("agentResearchAudit.runtimeEvents")}</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {runtime.events_truncated && <p className="text-sm text-muted-foreground">{t("agentResearchAudit.eventsTruncated")}</p>}
          {runtime.events.map((event) => (
            <div key={event.event_id} className="grid gap-1 rounded-md border p-3 text-sm sm:grid-cols-[4rem_1fr_auto]">
              <span className="text-muted-foreground">#{event.sequence}</span>
              <span>{event.event_type}</span>
              <time className="text-muted-foreground" dateTime={event.created_at}>{formatDate(event.created_at)}</time>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>{t("audit.runtimeSteps")}</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {runtime.audit_steps.map((step) => (
            <div key={`${step.ordinal}:${step.operation}`} className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3 text-sm">
              <span>{step.ordinal}. {step.operation}</span>
              <StatusBadge semantic={step.status === "failed" ? "failure" : "success"} label={step.status} />
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="break-words">{value}</div>
    </div>
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
