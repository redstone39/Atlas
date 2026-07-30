import { useTranslation } from "react-i18next";

import { Button } from "../../components/ui/button";
import { StatusBadge } from "../../shared/product-ui";
import type { ClaimedEvidenceTrace as ClaimedEvidenceTraceItem } from "./types";

export function ClaimedEvidenceTrace({
  items,
  showEmpty = false,
  onOpen,
}: {
  items?: ClaimedEvidenceTraceItem[];
  showEmpty?: boolean;
  onOpen?: (protectedOpenRef: string) => void;
}) {
  const { t } = useTranslation();
  const visibleItems = items ?? [];
  if (visibleItems.length === 0 && !showEmpty) return null;

  return (
    <section
      aria-label={t("claimedEvidence.title")}
      data-testid="model-claimed-evidence"
      className="rounded-md border border-dashed bg-muted/30 p-3"
    >
      <div className="font-medium">{t("claimedEvidence.title")}</div>
      <p className="mt-1 text-xs text-muted-foreground">
        {t("claimedEvidence.description")}
      </p>
      {visibleItems.length === 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          {t("claimedEvidence.empty")}
        </p>
      )}
      <ol className="mt-3 grid gap-3">
        {visibleItems.map((item) => (
          <li
            key={`${item.position}-${item.handle}`}
            className="rounded-md border bg-background p-3 text-sm"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <code className="break-all text-xs">{item.handle}</code>
              <StatusBadge
                semantic={
                  item.resolution_status === "resolved"
                    ? "success"
                    : item.resolution_status === "access_required"
                      ? "attention"
                      : "unknown"
                }
                label={t(`claimedEvidence.status.${item.resolution_status}`)}
              />
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {t("claimedEvidence.position", { position: item.position })}
              {item.duplicate_of_position
                ? ` · ${t("claimedEvidence.duplicate", {
                    position: item.duplicate_of_position,
                  })}`
                : ""}
            </div>
            {item.review_resolution_reason && (
              <div className="mt-2 text-xs text-muted-foreground">
                {t("claimedEvidence.reviewResolutionReason", {
                  reason: t(`claimedEvidence.reason.${item.review_resolution_reason}`, {
                    defaultValue: item.review_resolution_reason,
                  }),
                })}
              </div>
            )}
            {item.resolution_status === "resolved" && (
              <>
              <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                <TraceValue
                  label={t("claimedEvidence.document")}
                  value={item.document_display_name}
                />
                <TraceValue
                  label={t("claimedEvidence.locator")}
                  value={item.locator_label}
                />
                <TraceValue
                  label={t("claimedEvidence.evidenceRef")}
                  value={item.evidence_ref}
                />
                <TraceValue
                  label={t("claimedEvidence.resultRef")}
                  value={item.result_ref}
                />
                <TraceValue
                  label={t("claimedEvidence.invocationOrdinal")}
                  value={item.invocation_ordinal}
                />
                <TraceValue
                  label={t("claimedEvidence.documentRef")}
                  value={item.document_ref}
                />
                <TraceValue
                  label={t("claimedEvidence.documentVersionRef")}
                  value={item.document_version_ref}
                />
                <TraceValue
                  label={t("claimedEvidence.processingRevisionRef")}
                  value={item.processing_revision_ref}
                />
                <TraceValue
                  label={t("claimedEvidence.processingGenerationRef")}
                  value={item.processing_generation_ref}
                />
                <TraceValue
                  label={t("claimedEvidence.indexGenerationRef")}
                  value={item.index_generation_ref}
                />
              </dl>
              {item.protected_open_ref && onOpen && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={() => onOpen(item.protected_open_ref!)}
                >
                  {t("claimedEvidence.open")}
                </Button>
              )}
              </>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function TraceValue({
  label,
  value,
}: {
  label: string;
  value: string | number | null;
}) {
  if (value === null) return null;
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-all font-mono">{value}</dd>
    </div>
  );
}
