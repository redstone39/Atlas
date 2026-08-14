import type {
  ModelRouteRuntimePolicy,
  ModelRouteRuntimePolicyInput,
  ModelRouteStatus,
} from "./types";

export type RuntimePolicyDraft = Record<
  | "tokenizer_profile"
  | "max_tool_executions"
  | "max_provider_invocations"
  | "max_reasoning_revision_cycles"
  | "max_catalog_pages"
  | "max_search_rounds"
  | "max_model_visible_items_per_turn"
  | "max_retrieval_repairs"
  | "max_schema_retries_per_turn"
  | "max_selected_anchor_pages_per_round"
  | "provider_invocation_timeout_seconds"
  | "tool_execution_timeout_seconds"
  | "turn_timeout_seconds"
  | "context_window_tokens"
  | "max_input_tokens_per_invocation"
  | "max_output_tokens_per_invocation"
  | "max_tool_result_tokens_per_execution"
  | "max_total_tokens_per_conversation",
  string
>;

export const createRuntimePolicyDraft: RuntimePolicyDraft = {
  tokenizer_profile: "cl100k_base", max_tool_executions: "12",
  max_provider_invocations: "26", max_reasoning_revision_cycles: "2",
  max_catalog_pages: "5", max_search_rounds: "6",
  max_model_visible_items_per_turn: "40", max_retrieval_repairs: "3",
  max_schema_retries_per_turn: "3", max_selected_anchor_pages_per_round: "20",
  provider_invocation_timeout_seconds: "60", tool_execution_timeout_seconds: "45",
  turn_timeout_seconds: "240", context_window_tokens: "400000",
  max_input_tokens_per_invocation: "272000", max_output_tokens_per_invocation: "16000",
  max_tool_result_tokens_per_execution: "64000", max_total_tokens_per_conversation: "1000000",
};

export function runtimePolicyDraft(policy: ModelRouteRuntimePolicy): RuntimePolicyDraft {
  return Object.fromEntries(
    Object.entries(policy)
      .filter(([key]) => key !== "schema_version" && key !== "revision")
      .map(([key, value]) => [key, String(value)]),
  ) as RuntimePolicyDraft;
}

export function currentTestedTextDefaultRoute(
  routes: ModelRouteStatus[],
  textDefaultRouteId: string | null,
) {
  return routes.find((route) =>
    route.route_id === textDefaultRouteId && route.is_text_default &&
    route.enabled && route.status === "test_passed");
}

export function parseRuntimePolicy(
  draft: RuntimePolicyDraft,
): ModelRouteRuntimePolicyInput | null {
  const numericKeys = Object.keys(draft).filter((key) => key !== "tokenizer_profile") as
    Array<Exclude<keyof RuntimePolicyDraft, "tokenizer_profile">>;
  const values = Object.fromEntries(numericKeys.map((key) => [key, Number(draft[key])])) as
    Record<(typeof numericKeys)[number], number>;
  if (!draft.tokenizer_profile.trim()) return null;
  if (numericKeys.some((key) => !draft[key].trim() || !Number.isInteger(values[key]) ||
    (key === "max_reasoning_revision_cycles" ? values[key] < 0 : values[key] <= 0))) return null;
  if (values.max_reasoning_revision_cycles > 3 ||
    values.max_provider_invocations < values.max_tool_executions + 4 * values.max_reasoning_revision_cycles + 6) return null;
  if (values.max_retrieval_repairs > 3 || values.max_schema_retries_per_turn > 3 ||
    values.max_selected_anchor_pages_per_round > 20) return null;
  if (values.max_input_tokens_per_invocation + values.max_output_tokens_per_invocation > values.context_window_tokens ||
    values.max_tool_result_tokens_per_execution > values.max_input_tokens_per_invocation ||
    values.max_total_tokens_per_conversation < values.max_input_tokens_per_invocation + values.max_output_tokens_per_invocation ||
    values.turn_timeout_seconds < values.provider_invocation_timeout_seconds ||
    values.turn_timeout_seconds < values.tool_execution_timeout_seconds) return null;
  return { schema_version: "model-route-runtime-policy-v8", tokenizer_profile: draft.tokenizer_profile.trim(), ...values };
}
