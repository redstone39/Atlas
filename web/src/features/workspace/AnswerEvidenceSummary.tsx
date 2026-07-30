import { useTranslation } from "react-i18next";

import { Button } from "../../components/ui/button";
import { StatusBadge } from "../../shared/product-ui";
import type {
  ClaimedEvidenceTrace,
  ConversationTurn,
} from "./types";

type VisibleSource = {
  key: string;
  label: string;
  protectedOpenRef: string;
};

export function AnswerEvidenceSummary({
  status,
  items,
  onOpen,
}: {
  status: ConversationTurn["evidence_review_status"];
  items?: ClaimedEvidenceTrace[];
  onOpen: (protectedOpenRef: string) => void;
}) {
  const { t } = useTranslation();
  if (!status) return null;

  const presentation = {
    evidence_aligned: {
      semantic: "success" as const,
      label: t("workspace.evidenceAligned"),
    },
    questionable: {
      semantic: "attention" as const,
      label: t("workspace.needsHumanReview"),
    },
  }[status];
  const sources = visibleSources(items ?? [], t);

  return (
    <section
      aria-label={t("workspace.answerEvidenceSummary")}
      className="flex flex-col gap-2"
    >
      <StatusBadge
        semantic={presentation.semantic}
        label={presentation.label}
        className="w-fit"
      />
      {sources.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="text-xs font-medium text-muted-foreground">
            {t("workspace.citedDocuments")}
          </div>
          <div className="flex flex-wrap gap-2">
            {sources.map((source) => (
              <Button
                key={source.key}
                type="button"
                variant="outline"
                size="sm"
                className="h-auto max-w-full whitespace-normal text-left"
                aria-label={t("workspace.openCitedDocument", {
                  source: source.label,
                })}
                onClick={() => onOpen(source.protectedOpenRef)}
              >
                {source.label}
              </Button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function visibleSources(
  items: ClaimedEvidenceTrace[],
  t: (key: string, options?: Record<string, unknown>) => string,
): VisibleSource[] {
  const seen = new Set<string>();
  const result: VisibleSource[] = [];
  for (const item of items) {
    const document = item.document_display_name?.trim();
    if (
      item.resolution_status !== "resolved"
      || !document
      || !item.protected_open_ref
    ) {
      continue;
    }
    const key = `${item.document_ref ?? document}\u0000${item.page_number ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({
      key,
      label: item.page_number === null
        ? document
        : t("workspace.citedDocumentPage", {
            document,
            page: item.page_number,
          }),
      protectedOpenRef: item.protected_open_ref,
    });
  }
  return result;
}
