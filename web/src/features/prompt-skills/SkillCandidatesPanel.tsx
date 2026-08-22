"use client";

import type { TFunction } from "i18next";
import { FileSearch, RefreshCw, ShieldCheck, ShieldX } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "../../components/ui/alert-dialog";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "../../components/ui/empty";
import { Spinner } from "../../components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { ApiError, localizeMessage } from "../../shared/user-messages";
import { promptSkillsApi } from "./api";
import type {
  PromptSkillCategory,
  SkillCandidateDetail,
  SkillCandidateStatus,
  SkillCandidateSummary,
} from "./types";

type CandidateAction = "approve" | "reject";
type PendingCandidateMutation = {
  action: CandidateAction;
  candidateRef: string;
  expectedDraftRevision: number;
  idempotencyKey: string;
};

function statusLabel(t: TFunction, status: SkillCandidateStatus) {
  return t(`promptSkills.candidateStatus.${status}`);
}

export function SkillCandidatesPanel({
  category,
  onApproved,
}: {
  category: PromptSkillCategory;
  onApproved: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const [candidates, setCandidates] = useState<SkillCandidateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillCandidateDetail | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [pendingMutation, setPendingMutation] =
    useState<PendingCandidateMutation | null>(null);
  const [mutationNeedsRetry, setMutationNeedsRetry] = useState(false);

  async function refreshCandidates() {
    setLoading(true);
    setLoadError(null);
    try {
      const result = await promptSkillsApi.listCandidates(category);
      setCandidates(result.items);
    } catch (cause) {
      setLoadError(
        cause instanceof ApiError
          ? localizeMessage(
              cause.reference,
              t,
              "promptSkills.candidateLoadFailed",
            )
          : t("promptSkills.candidateLoadFailed"),
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshCandidates();
  }, [category]);

  async function viewCandidate(candidateRef: string) {
    setBusyKey(`${candidateRef}:detail`);
    try {
      setDetail(await promptSkillsApi.getCandidate(candidateRef));
      setPendingMutation(null);
      setMutationNeedsRetry(false);
    } catch (cause) {
      toast.error(
        cause instanceof ApiError
          ? localizeMessage(
              cause.reference,
              t,
              "promptSkills.candidateActionFailed",
            )
          : t("promptSkills.candidateActionFailed"),
      );
    } finally {
      setBusyKey(null);
    }
  }

  async function reloadSelected(candidateRef: string) {
    await refreshCandidates();
    setDetail(await promptSkillsApi.getCandidate(candidateRef));
  }

  async function mutateCandidate(
    action: CandidateAction,
    retry: PendingCandidateMutation | null = null,
  ) {
    if (!detail && retry === null) return;
    const request =
      retry ??
      ({
        action,
        candidateRef: detail!.candidate_ref,
        expectedDraftRevision: detail!.draft_revision,
        idempotencyKey: crypto.randomUUID(),
      } satisfies PendingCandidateMutation);
    setPendingMutation(request);
    setMutationNeedsRetry(false);
    setBusyKey(`${request.candidateRef}:${request.action}`);
    try {
      const outcome = await promptSkillsApi.mutateCandidate(
        request.candidateRef,
        request.action,
        request.expectedDraftRevision,
        request.idempotencyKey,
      );
      setPendingMutation(null);
      if (outcome.status === "stale") {
        toast.error(t("promptSkills.candidateStaleReloaded"));
      } else {
        toast.success(
          t(
            request.action === "approve"
              ? "promptSkills.candidateApproved"
              : "promptSkills.candidateRejected",
          ),
        );
        if (request.action === "approve") {
          await onApproved();
        }
      }
      await reloadSelected(request.candidateRef);
    } catch (cause) {
      if (cause instanceof ApiError) {
        setPendingMutation(null);
        if (cause.status === 412) {
          toast.error(t("promptSkills.candidateStaleReloaded"));
          await reloadSelected(request.candidateRef);
        } else {
          toast.error(
            localizeMessage(
              cause.reference,
              t,
              "promptSkills.candidateActionFailed",
            ),
          );
        }
      } else {
        setMutationNeedsRetry(true);
        toast.error(t("promptSkills.candidateMutationUncertain"));
      }
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <CardTitle>{t("promptSkills.candidatesTitle")}</CardTitle>
          <CardDescription>
            {t("promptSkills.candidatesDescription")}
          </CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refreshCandidates()}
          disabled={loading || busyKey !== null}
        >
          <RefreshCw data-icon="inline-start" />
          {t("promptSkills.refreshCandidates")}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {loadError && (
          <Alert variant="destructive">
            <AlertTitle>{t("promptSkills.candidateLoadFailedTitle")}</AlertTitle>
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
        )}
        {loading && candidates.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Spinner />
            {t("promptSkills.candidatesLoading")}
          </div>
        ) : candidates.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <FileSearch />
              </EmptyMedia>
              <EmptyTitle>{t("promptSkills.candidatesEmptyTitle")}</EmptyTitle>
              <EmptyDescription>
                {t("promptSkills.candidatesEmptyDescription")}
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("promptSkills.candidate")}</TableHead>
                <TableHead>{t("promptSkills.candidateDisposition")}</TableHead>
                <TableHead>{t("promptSkills.status")}</TableHead>
                <TableHead>{t("promptSkills.updated")}</TableHead>
                <TableHead className="text-right">
                  {t("promptSkills.actions")}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {candidates.map((candidate) => {
                const detailKey = `${candidate.candidate_ref}:detail`;
                return (
                  <TableRow key={candidate.candidate_ref}>
                    <TableCell>
                      <div className="flex flex-col gap-1">
                        <span className="font-medium">{candidate.target_name}</span>
                        <span className="text-xs text-muted-foreground">
                          {candidate.topic}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {t(
                          candidate.disposition === "add"
                            ? "promptSkills.candidateAdd"
                            : "promptSkills.candidateRevise",
                        )}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">
                        {statusLabel(t, candidate.status)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {new Intl.DateTimeFormat(i18n.language, {
                        dateStyle: "medium",
                        timeStyle: "short",
                      }).format(new Date(candidate.updated_at))}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void viewCandidate(candidate.candidate_ref)}
                        disabled={busyKey !== null}
                      >
                        {busyKey === detailKey && <Spinner data-icon="inline-start" />}
                        {t("promptSkills.reviewCandidate")}
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <Dialog
        open={detail !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDetail(null);
            setPendingMutation(null);
            setMutationNeedsRetry(false);
          }
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {detail?.target_name ?? t("promptSkills.candidateDetail")}
            </DialogTitle>
            <DialogDescription>
              {detail?.goal ?? t("promptSkills.candidateDetailDescription")}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="flex flex-col gap-5">
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">
                  {t(
                    detail.disposition === "add"
                      ? "promptSkills.candidateAdd"
                      : "promptSkills.candidateRevise",
                  )}
                </Badge>
                <Badge variant="secondary">
                  {statusLabel(t, detail.status)}
                </Badge>
                <Badge variant="outline">
                  {t("promptSkills.candidateDraftRevision", {
                    revision: detail.draft_revision,
                  })}
                </Badge>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="flex flex-col gap-1">
                  <span className="text-sm font-medium">
                    {t("promptSkills.candidateRationale")}
                  </span>
                  <p className="text-sm text-muted-foreground">
                    {detail.rationale}
                  </p>
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-sm font-medium">
                    {t("promptSkills.candidateRisk")}
                  </span>
                  <p className="text-sm text-muted-foreground">{detail.risk}</p>
                </div>
              </div>
              <div className="flex flex-col gap-2">
                <span className="text-sm font-medium">
                  {t("promptSkills.candidateEvidence")}
                </span>
                <ul className="space-y-1 text-xs text-muted-foreground">
                  {detail.source_evidence.map((evidence) => (
                    <li
                      key={`${evidence.consolidation_ref}:${evidence.generalized_experience_ordinal}`}
                    >
                      {evidence.consolidation_ref} · #
                      {evidence.generalized_experience_ordinal}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="flex flex-col gap-2">
                <span className="text-sm font-medium">
                  {t("promptSkills.candidateSource")}
                </span>
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-4 text-xs">
                  {detail.skill_source}
                </pre>
              </div>
            </div>
          )}
          {detail?.status === "draft" && (
            <DialogFooter>
              {mutationNeedsRetry && pendingMutation ? (
                <Button
                  onClick={() =>
                    void mutateCandidate(pendingMutation.action, pendingMutation)
                  }
                  disabled={busyKey !== null}
                >
                  {busyKey !== null && <Spinner data-icon="inline-start" />}
                  {t("promptSkills.retryCandidateMutation")}
                </Button>
              ) : (
                <>
                  <CandidateConfirmation
                    action="reject"
                    targetName={detail.target_name}
                    disabled={busyKey !== null}
                    onConfirm={() => void mutateCandidate("reject")}
                    t={t}
                  />
                  <CandidateConfirmation
                    action="approve"
                    targetName={detail.target_name}
                    disabled={busyKey !== null}
                    onConfirm={() => void mutateCandidate("approve")}
                    t={t}
                  />
                </>
              )}
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function CandidateConfirmation({
  action,
  targetName,
  disabled,
  onConfirm,
  t,
}: {
  action: CandidateAction;
  targetName: string;
  disabled: boolean;
  onConfirm: () => void;
  t: TFunction;
}) {
  const approve = action === "approve";
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant={approve ? "default" : "outline"} disabled={disabled}>
          {approve ? (
            <ShieldCheck data-icon="inline-start" />
          ) : (
            <ShieldX data-icon="inline-start" />
          )}
          {t(
            approve
              ? "promptSkills.approveCandidate"
              : "promptSkills.rejectCandidate",
          )}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t(
              approve
                ? "promptSkills.approveCandidateTitle"
                : "promptSkills.rejectCandidateTitle",
              { name: targetName },
            )}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {t(
              approve
                ? "promptSkills.approveCandidateDescription"
                : "promptSkills.rejectCandidateDescription",
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant={approve ? "default" : "destructive"}
            onClick={onConfirm}
          >
            {t("promptSkills.confirmCandidateAction")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
