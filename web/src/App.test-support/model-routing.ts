import type {
  AnswerBehaviorStatus,
  ModelRouteStatus,
  ProviderConnectionStatus,
} from "../features/model-routing/index";
import type { ModelRouteRuntimePolicy } from "../features/model-routing/types";

import type { MockApiHandler } from "./protocol";
import { jsonResponse } from "./protocol";

export function createModelRoutingHandler(
  options: { modelRoutes?: ModelRouteStatus[] } = {},
): MockApiHandler {
  let answerBehavior: AnswerBehaviorStatus = {
    revision: 0,
    custom_guidance: null,
    guidance_digest: null,
    updated_by: null,
    updated_at: null,
    audit_event_ref: null,
  };
  let providerConnections: ProviderConnectionStatus[] = [
    {
      connection_id: "connection-openai-primary",
      display_name: "OpenAI production",
      provider_type: "openai_compatible",
      endpoint_url: "https://api.openai.com/v1",
      api_version: null,
      credential_configured: true,
      status: "verified",
      enabled: true,
      linked_model_count: 2,
      revision: 2,
      last_verified_at: "2026-07-10T01:00:00Z",
      last_rotated_at: "2026-07-10T01:00:00Z",
      message_code: "provider.connection_is_verified", message_params: {},
      audit_event_ref: "audit-provider-connection-primary",
    },
    {
      connection_id: "connection-migrated-required",
      display_name: "Migrated provider",
      provider_type: "openai_compatible",
      endpoint_url: "https://provider.example/v1",
      api_version: null,
      credential_configured: false,
      status: "credential_required",
      enabled: false,
      linked_model_count: 0,
      revision: 1,
      last_verified_at: null,
      last_rotated_at: null,
      message_code: "provider.api_key_is_required", message_params: {},
      audit_event_ref: "audit-provider-connection-migrated",
    },
    {
      connection_id: "connection-manual-entry",
      display_name: "Manual provider",
      provider_type: "azure_openai",
      endpoint_url: "https://example.openai.azure.com",
      api_version: "2024-10-21",
      credential_configured: true,
      status: "configured",
      enabled: false,
      linked_model_count: 0,
      revision: 1,
      last_verified_at: null,
      last_rotated_at: "2026-07-10T01:30:00Z",
      message_code: "model.provider_model_discovery_is_unavailable", message_params: {},
      audit_event_ref: "audit-provider-connection-manual",
    },
  ];
  const runtimePolicy = (
    revision: number,
    overrides: Partial<Omit<ModelRouteRuntimePolicy, "revision">> = {},
  ) => ({
    schema_version: "model-route-runtime-policy-v8" as const,
    tokenizer_profile: "cl100k_base",
    max_tool_executions: 3,
    max_provider_invocations: 20,
    max_reasoning_revision_cycles: 2,
    max_catalog_pages: 5,
    max_search_rounds: 6,
    max_model_visible_items_per_turn: 40,
  max_retrieval_repairs: 3,
  max_schema_retries_per_turn: 3,
  max_selected_anchor_pages_per_round: 20,
    provider_invocation_timeout_seconds: 30,
    tool_execution_timeout_seconds: 20,
    turn_timeout_seconds: 90,
    context_window_tokens: 16_000,
    max_input_tokens_per_invocation: 8_000,
    max_output_tokens_per_invocation: 2_000,
    max_tool_result_tokens_per_execution: 4_000,
    max_total_tokens_per_conversation: 20_000,
    ...overrides,
    revision,
  });
  let modelRoutes: ModelRouteStatus[] = options.modelRoutes ?? [
    {
      route_id: "route-primary-provider",
      display_name: "Primary provider",
      provider_type: "openai_compatible",
      model_name: "gpt-4.1-mini",
      connection_id: "connection-openai-primary",
      status: "test_passed",
      message_code: "model.provider_model_route_passed_the_controlled_test", message_params: {},
      enabled: true,
      supports_vision: true,
      revision: 2,
      runtime_policy: runtimePolicy(2),
      audit_event_ref: "audit-model-route-primary",
      is_text_default: true,
      is_vision_default: true,
    },
    {
      route_id: "route-secondary-provider",
      display_name: "Secondary provider",
      provider_type: "openai_compatible",
      model_name: "gpt-4.1",
      connection_id: "connection-openai-primary",
      status: "configured",
      message_code: "model.route_is_configured", message_params: {},
      enabled: true,
      supports_vision: true,
      revision: 1,
      runtime_policy: runtimePolicy(1, {
        tokenizer_profile: "o200k_base",
        max_tool_executions: 2,
        max_provider_invocations: 20,
        max_catalog_pages: 5,
        max_search_rounds: 6,
        max_model_visible_items_per_turn: 40,
  max_retrieval_repairs: 3,
  max_schema_retries_per_turn: 3,
  max_selected_anchor_pages_per_round: 20,
        provider_invocation_timeout_seconds: 45,
        tool_execution_timeout_seconds: 30,
        turn_timeout_seconds: 120,
        context_window_tokens: 32_000,
        max_input_tokens_per_invocation: 24_000,
        max_output_tokens_per_invocation: 4_000,
        max_tool_result_tokens_per_execution: 8_000,
        max_total_tokens_per_conversation: 48_000,
      }),
      audit_event_ref: "audit-model-route-secondary",
      is_text_default: false,
      is_vision_default: false,
    },
  ];
  return ({ url, method, init }) => {
    if (
      url.pathname === "/api/v1/admin/config/provider-connections" &&
      method === "GET"
    ) {
      return jsonResponse({ connections: providerConnections });
    }
    if (url.pathname === "/api/v1/admin/answer-behavior" && method === "GET") {
      return jsonResponse(answerBehavior);
    }
    if (url.pathname === "/api/v1/admin/answer-behavior" && method === "PUT") {
      const payload = JSON.parse(String(init?.body ?? "{}"));
      if (payload.expected_revision !== answerBehavior.revision) {
        return jsonResponse(
          {
            error_code: "revision_conflict",
            message_code: "answer_behavior.revision_changed_before_update",
            message_params: {},
          },
          409,
        );
      }
      const customGuidance =
        typeof payload.custom_guidance === "string"
          ? payload.custom_guidance.trim() || null
          : null;
      answerBehavior = {
        revision: answerBehavior.revision + 1,
        custom_guidance: customGuidance,
        guidance_digest: "a".repeat(64),
        updated_by: "user-admin-001",
        updated_at: "2026-07-27T08:00:00Z",
        audit_event_ref: `audit-answer-behavior-${answerBehavior.revision + 1}`,
      };
      return jsonResponse(answerBehavior);
    }
    if (
      url.pathname === "/api/v1/admin/config/provider-connections" &&
      method === "POST"
    ) {
      const payload = JSON.parse(String(init?.body ?? "{}"));
      const connection: ProviderConnectionStatus = {
        connection_id: payload.connection_id,
        display_name: payload.display_name,
        provider_type: payload.provider_type,
        endpoint_url: payload.endpoint_url,
        api_version: payload.api_version ?? null,
        credential_configured: true,
        status: "verified",
        enabled: true,
        linked_model_count: 0,
        revision: 1,
        last_verified_at: "2026-07-10T02:00:00Z",
        last_rotated_at: "2026-07-10T02:00:00Z",
        message_code: "provider.connection_is_verified", message_params: {},
        audit_event_ref: "audit-provider-connection-created",
      };
      providerConnections = [...providerConnections, connection];
      return jsonResponse(connection, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/provider-connections/") &&
      method === "PATCH"
    ) {
      const connectionId = url.pathname.split("/").at(-1) ?? "";
      const payload = JSON.parse(String(init?.body ?? "{}"));
      const current = providerConnections.find(
        (connection) => connection.connection_id === connectionId,
      )!;
      const updated: ProviderConnectionStatus = {
        ...current,
        display_name: payload.display_name ?? current.display_name,
        endpoint_url: payload.endpoint_url ?? current.endpoint_url,
        api_version:
          payload.api_version === undefined ? current.api_version : payload.api_version,
        credential_configured: current.credential_configured || Boolean(payload.api_key),
        status: payload.api_key ? "verified" : current.status,
        enabled: payload.enabled ?? current.enabled,
        revision: current.revision + 1,
        last_rotated_at: payload.api_key
          ? "2026-07-10T03:00:00Z"
          : current.last_rotated_at,
        message_code: "provider.connection_is_updated", message_params: {},
        audit_event_ref: "audit-provider-connection-updated",
      };
      providerConnections = providerConnections.map((connection) =>
        connection.connection_id === connectionId ? updated : connection,
      );
      return jsonResponse(updated);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/provider-connections/") &&
      url.pathname.endsWith("/test") &&
      method === "POST"
    ) {
      const connectionId = url.pathname.split("/").at(-2) ?? "";
      const connection = providerConnections.find(
        (candidate) => candidate.connection_id === connectionId,
      )!;
      return jsonResponse({
        connection,
        validation_status: "passed",
        tested_route_ids: modelRoutes
          .filter((route) => route.connection_id === connectionId)
          .map((route) => route.route_id),
        message_code: "provider.connection_test_passed", message_params: {},
        audit_event_ref: "audit-provider-connection-tested",
      });
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/provider-connections/") &&
      url.pathname.endsWith("/available-models") &&
      method === "GET"
    ) {
      const connectionId = url.pathname.split("/").at(-2) ?? "";
      const connection = providerConnections.find(
        (candidate) => candidate.connection_id === connectionId,
      );
      if (
        !connection?.credential_configured ||
        connectionId === "connection-manual-entry"
      ) {
        return jsonResponse({
          connection_id: connectionId,
          discovery_status: "unavailable",
          models: [],
          message_code: "model.provider_model_discovery_is_unavailable", message_params: {},
        });
      }
      return jsonResponse({
        connection_id: connectionId,
        discovery_status: "available",
        models: ["gpt-4.1", "gpt-4.1-mini"],
        message_code: "model.provider_models_are_available", message_params: {},
      });
    }
    if (url.pathname === "/api/v1/admin/config/model-routes" && method === "GET") {
      return jsonResponse({
        text_default_route_id:
          modelRoutes.find((route) => route.is_text_default)?.route_id ?? null,
        vision_default_route_id:
          modelRoutes.find((route) => route.is_vision_default)?.route_id ?? null,
        routes: modelRoutes,
      });
    }
    if (url.pathname === "/api/v1/admin/config/model-routes" && method === "POST") {
      const payload = JSON.parse(String(init?.body ?? "{}"));
      const route: ModelRouteStatus = {
        route_id: payload.route_id,
        display_name: payload.display_name,
        provider_type:
          providerConnections.find(
            (connection) => connection.connection_id === payload.connection_id,
          )?.provider_type ?? "openai_compatible",
        model_name: payload.model_name,
        connection_id: payload.connection_id,
        status: "configured",
        message_code: "model.route_is_configured", message_params: {},
        enabled: payload.enabled,
        supports_vision: payload.supports_vision ?? false,
        revision: 1,
        runtime_policy: { ...payload.runtime_policy, revision: 1 },
        audit_event_ref: "audit-model-route-configured",
        is_text_default: false,
        is_vision_default: false,
      };
      modelRoutes = [...modelRoutes, route];
      return jsonResponse(route, 201);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/model-routes/") &&
      !url.pathname.endsWith("/test") &&
      method === "PATCH"
    ) {
      const routeId = url.pathname.split("/").at(-1) ?? "";
      const payload = JSON.parse(String(init?.body ?? "{}"));
      const current = modelRoutes.find((candidate) => candidate.route_id === routeId)!;
      const connectionId = payload.connection_id ?? current.connection_id;
      const updated: ModelRouteStatus = {
        ...current,
        display_name: payload.display_name ?? current.display_name,
        model_name: payload.model_name ?? current.model_name,
        connection_id: connectionId,
        provider_type:
          providerConnections.find(
            (connection) => connection.connection_id === connectionId,
          )?.provider_type ?? current.provider_type,
        enabled: payload.enabled ?? current.enabled,
        supports_vision: payload.supports_vision ?? current.supports_vision,
        revision: current.revision + 1,
        runtime_policy: {
          ...payload.runtime_policy,
          revision: current.runtime_policy.revision + 1,
        },
        message_code: "model.route_is_updated", message_params: {},
        audit_event_ref: "audit-model-route-updated",
      };
      modelRoutes = modelRoutes.map((route) =>
        route.route_id === routeId ? updated : route,
      );
      return jsonResponse(updated);
    }
    if (
      url.pathname.startsWith("/api/v1/admin/config/model-routes/") &&
      url.pathname.endsWith("/test") &&
      method === "POST"
    ) {
      const routeId = url.pathname.split("/").at(-2) ?? "";
      const route = modelRoutes.find((candidate) => candidate.route_id === routeId)!;
      const nextRoute = {
        ...route,
        status: "test_passed" as const,
        revision: route.revision + 1,
        message_code: "model.provider_model_route_passed_the_controlled_test", message_params: {},
        audit_event_ref: "audit-model-route-test-passed",
      };
      modelRoutes = modelRoutes.map((candidate) =>
        candidate.route_id === routeId ? nextRoute : candidate,
      );
      return jsonResponse(nextRoute);
    }
    const defaultRouteMatch = url.pathname.match(
      /^\/api\/v1\/admin\/config\/model-routes\/([^/]+)\/defaults\/(text|vision)$/,
    );
    if (defaultRouteMatch && method === "POST") {
      const [, routeId, purpose] = defaultRouteMatch;
      const currentRoute = modelRoutes.find((candidate) => candidate.route_id === routeId)!;
      if (
        currentRoute.status !== "test_passed" ||
        (purpose === "vision" && !currentRoute.supports_vision)
      ) {
        return jsonResponse(
          {
            ...currentRoute,
            message_code:
              purpose === "vision" && !currentRoute.supports_vision
                ? "model.vision_default_requires_vision_capability"
                : "model.test_this_route_before_making_it_default",
            message_params: {},
            audit_event_ref: "audit-model-route-default-rejected",
          },
          422,
        );
      }
      modelRoutes = modelRoutes.map((route) => ({
        ...route,
        is_text_default:
          purpose === "text" ? route.route_id === routeId : route.is_text_default,
        is_vision_default:
          purpose === "vision" ? route.route_id === routeId : route.is_vision_default,
      }));
      const route = modelRoutes.find((candidate) => candidate.route_id === routeId)!;
      return jsonResponse({
        ...route,
        message_code:
          purpose === "text"
            ? "model.default_text_model_route_is_updated"
            : "model.default_vision_model_route_is_updated",
        message_params: {},
        audit_event_ref: "audit-model-route-default-updated",
      });
    }
    return undefined;
  };
}
