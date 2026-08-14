import type { ConversationTurn } from "../workspace/index";
import type { DiscoveryCandidateTrace } from "./types";

export type DiscoveryPreview = Pick<
  DiscoveryCandidateTrace,
  "document_display_name" | "locator_label" | "preview"
>;

export function assistantAttemptPosition(
  turn: ConversationTurn,
  turns: ConversationTurn[],
) {
  if (turn.role !== "assistant" || !turn.source_turn_id) return null;

  const attempts = turns
    .map((candidate, index) => ({ candidate, index }))
    .filter(
      ({ candidate }) =>
        candidate.role === "assistant" &&
        candidate.source_turn_id === turn.source_turn_id,
    )
    .sort((left, right) => {
      const leftTime = Date.parse(left.candidate.created_at);
      const rightTime = Date.parse(right.candidate.created_at);
      if (Number.isNaN(leftTime) && Number.isNaN(rightTime)) {
        return left.index - right.index;
      }
      if (Number.isNaN(leftTime)) return 1;
      if (Number.isNaN(rightTime)) return -1;
      return leftTime === rightTime
        ? left.index - right.index
        : leftTime - rightTime;
    });
  const position = attempts.findIndex(({ candidate }) => candidate === turn);
  return position < 0
    ? null
    : { ordinal: position + 1, total: attempts.length };
}

export function AuditField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-medium">{label}</div>
      <div className="break-all text-muted-foreground">{value}</div>
    </div>
  );
}

export function AuditTraceValue({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-all font-mono">{value}</dd>
    </div>
  );
}

export function formatDateTime(value: string, locale: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "-";
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
