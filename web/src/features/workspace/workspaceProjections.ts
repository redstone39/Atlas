import { joinResponseSegmentMarkdown } from "./api";
import type {
  ConversationTurn,
  ConversationTurnResult,
  ReasoningProgress,
  ResponseSegment,
  RuntimeStreamEvent,
} from "./types";

export function resultToTurn(result: ConversationTurnResult): ConversationTurn {
  return {
    ...result,
    role: "assistant",
    input_text: null,
  };
}

export function projectRuntimeStreamEvent(
  event: RuntimeStreamEvent,
  eventType: string,
): { phase: string | null; progress: ReasoningProgress | null } {
  if (
    eventType !== "reasoning_progressed" ||
    !event.event_id ||
    !event.reasoning_phase ||
    !event.progress_status ||
    !event.created_at ||
    !event.message_code
  ) {
    return { phase: event.phase ?? null, progress: null };
  }
  return {
    phase: event.phase ?? null,
    progress: {
      event_id: event.event_id,
      sequence: event.sequence,
      phase: event.reasoning_phase,
      status: event.progress_status,
      cycle: event.cycle ?? null,
      message_code: event.message_code,
      message_params: event.message_params ?? {},
      created_at: event.created_at,
    },
  };
}

export function mergeReasoningProgress(
  current: ReasoningProgress[],
  progress: ReasoningProgress,
) {
  return [
    ...current.filter((item) => item.event_id !== progress.event_id),
    progress,
  ].sort((left, right) => left.sequence - right.sequence);
}

export function mergeStreamingSegment(
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

export function answerMarkdownText(turn: ConversationTurn) {
  return turn.answer_text ?? joinResponseSegmentMarkdown(turn.response_segments);
}

export function processingRuntimePhase(
  turn: ConversationTurn,
  runtimeProgress: string,
) {
  return turn.reasoning_timeline.at(-1)?.phase ?? runtimeProgress;
}

export function messageTime(value: string, locale: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function sliceCodePoints(value: string, start: number, end?: number) {
  return Array.from(value).slice(start, end).join("");
}

export function claimsInPresentationOrder(
  segment: ResponseSegment,
): ResponseSegment["claims"] {
  return [...segment.claims].sort(
    (left, right) => left.start - right.start || left.end - right.end,
  );
}
