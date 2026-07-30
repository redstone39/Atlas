import { CircleStop, Clock3, RefreshCw, TimerReset } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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
import { Button } from "../../components/ui/button";
import { Progress } from "../../components/ui/progress";
import { Spinner } from "../../components/ui/spinner";
import { StatusBadge, serverMessage } from "../../shared/product-ui";
import {
  documentLibraryProductStatusLabel,
  documentLibraryProductStatusSemantic,
  intakeStatusLabel,
  intakeStatusSemantic,
  type DocumentLibraryProductStatus,
} from "../../shared/document-status";
import { processingJobApi } from "./api";
import { ACTIVE_PROCESSING_STATUSES, type ProcessingJobStatus } from "./types";

export function ProcessingJobPanel({
  job,
  onChanged,
  displayStatus,
}: {
  job: ProcessingJobStatus;
  onChanged: () => Promise<void>;
  displayStatus?: DocumentLibraryProductStatus;
}) {
  const { t, i18n } = useTranslation();
  const [pendingCommand, setPendingCommand] = useState<"cancel" | "retry" | "">("");
  const [clock, setClock] = useState(() => Date.now());
  const [elapsedAnchor, setElapsedAnchor] = useState(() => ({
    key: `${job.updated_at}:${job.elapsed_seconds}:${job.status}`,
    receivedAt: Date.now(),
  }));
  const active = ACTIVE_PROCESSING_STATUSES.has(job.status);
  const anchorKey = `${job.updated_at}:${job.elapsed_seconds}:${job.status}`;

  useEffect(() => {
    setElapsedAnchor({ key: anchorKey, receivedAt: Date.now() });
    setClock(Date.now());
  }, [anchorKey]);

  useEffect(() => {
    if (!active) return;
    const interval = window.setInterval(() => setClock(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, [active]);

  const elapsedSeconds = job.elapsed_seconds + (
    active && elapsedAnchor.key === anchorKey
      ? Math.max(0, Math.floor((clock - elapsedAnchor.receivedAt) / 1_000))
      : 0
  );
  const percentage = useMemo(() => {
    if (job.progress_total === null || job.progress_total <= 0) return null;
    return Math.min(100, Math.round((job.progress_current / job.progress_total) * 100));
  }, [job.progress_current, job.progress_total]);

  async function runCommand(command: "cancel" | "retry") {
    setPendingCommand(command);
    try {
      await processingJobApi[command](job.job_id);
      toast.success(t(command === "cancel" ? "processing.stopSucceeded" : "processing.retrySucceeded"));
      await onChanged();
    } catch (err) {
      toast.error(serverMessage(err instanceof Error ? err.message : "admin.actionFailed", t));
    } finally {
      setPendingCommand("");
    }
  }

  return (
    <div className="rounded-md border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              semantic={
                displayStatus
                  ? documentLibraryProductStatusSemantic(displayStatus)
                  : intakeStatusSemantic(job.status)
              }
              label={
                displayStatus
                  ? documentLibraryProductStatusLabel(displayStatus, t)
                  : intakeStatusLabel(job.status, t)
              }
            />
            {!displayStatus && job.current_stage && (
              <span className="text-sm text-muted-foreground">
                {t(`processing.stage.${job.current_stage}`, { defaultValue: job.current_stage })}
              </span>
            )}
          </div>
          <div className="text-sm font-medium">{t("processing.currentAttempt")}</div>
          <div className="text-sm text-muted-foreground">
            {t("plugins.profile")}: {job.profile_id
              ? `${job.profile_id}${job.profile_revision ? ` r${job.profile_revision}` : ""}`
              : "—"}
            {` · ${job.document_format.toUpperCase()}`}
          </div>
          {job.warning_codes.length > 0 && (
            <div className="text-sm text-warning">
              {t("ingestion.processingWarnings", {
                warnings: job.warning_codes.map((code) => processingCodeLabel(code, t)).join(", "),
              })}
            </div>
          )}
          {job.failure_code && (
            <div className="text-sm text-destructive">
              {processingCodeLabel(job.failure_code, t)}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {job.cancel_available && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="sm" disabled={Boolean(pendingCommand)}>
                  {pendingCommand === "cancel" ? <Spinner /> : <CircleStop data-icon="inline-start" />}
                  {t("processing.stop")}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{t("processing.stopConfirmTitle")}</AlertDialogTitle>
                  <AlertDialogDescription>{t("processing.stopConfirmDescription")}</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t("admin.cancel")}</AlertDialogCancel>
                  <AlertDialogAction
                    variant="destructive"
                    onClick={() => void runCommand("cancel")}
                  >
                    {t("processing.stop")}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
          {job.retry_available && (
            <Button
              variant="outline"
              size="sm"
              disabled={Boolean(pendingCommand)}
              onClick={() => void runCommand("retry")}
            >
              {pendingCommand === "retry" ? <Spinner /> : <RefreshCw data-icon="inline-start" />}
              {t("processing.retry")}
            </Button>
          )}
        </div>
      </div>

      <div className="mt-4 space-y-2">
        <div className="flex items-center justify-between gap-3 text-sm">
          <span>{t("processing.progress")}</span>
          <span className="tabular-nums text-muted-foreground">
            {job.progress_total === null
              ? t("processing.progressCommitted", {
                  current: job.progress_current,
                  unit: t(`processing.unit.${job.progress_unit}`),
                })
              : t("processing.progressOfTotal", {
                  current: job.progress_current,
                  total: job.progress_total,
                  unit: t(`processing.unit.${job.progress_unit}`),
                })}
          </span>
        </div>
        {percentage === null ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <Spinner />
            {t("processing.totalPending")}
          </div>
        ) : (
          <Progress value={percentage} aria-label={t("processing.progressPercent", { percentage })} />
        )}
      </div>

      <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
        <div className="flex items-center gap-2">
          <Clock3 className="size-4 text-muted-foreground" aria-hidden="true" />
          <dt className="text-muted-foreground">{t("processing.elapsed")}</dt>
          <dd className="font-medium tabular-nums">{formatDuration(elapsedSeconds)}</dd>
        </div>
        <div className="flex items-center gap-2">
          <TimerReset className="size-4 text-muted-foreground" aria-hidden="true" />
          <dt className="text-muted-foreground">{t("processing.lastUpdated")}</dt>
          <dd className="font-medium">
            {new Intl.DateTimeFormat(i18n.language, {
              dateStyle: "short",
              timeStyle: "medium",
            }).format(new Date(job.updated_at))}
          </dd>
        </div>
      </dl>
    </div>
  );
}

export function formatDuration(totalSeconds: number) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainder = seconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function processingCodeLabel(
  value: string,
  t: (key: string, options?: { defaultValue?: string }) => string,
) {
  if (value === "pdf_preview_unavailable") {
    return t("ingestion.pdfPreviewUnavailable");
  }
  const knownLabels: Record<string, string> = {
    office_preview_unavailable: "Office preview unavailable",
    office_preview_page_mapping_missing: "Office preview page mapping missing",
    image_ocr_failed: "Image OCR failed",
    visual_interpretation_failed: "Visual interpretation failed",
    optional_processor_failed: "Optional processor failed",
    legacy_converter_unavailable: "Legacy converter unavailable",
    no_searchable_evidence: "No searchable evidence",
  };
  if (knownLabels[value]) return knownLabels[value];
  return value
    .replace(/[:_]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}
