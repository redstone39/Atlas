import { requestJson } from "../../shared/api-client";
import { clientRequestId } from "../../shared/ids";
import type {
  AnswerBehaviorStatus,
  AnswerBehaviorUpdateInput,
  AvailableModelsResult,
  ModelRouteConfigInput,
  ModelRouteListResult,
  ModelRouteStatus,
  ModelRouteUpdateInput,
  ProviderConnectionCreateInput,
  ProviderConnectionListResult,
  ProviderConnectionStatus,
  ProviderConnectionTestResult,
  ProviderConnectionUpdateInput,
} from "./types";


export const modelRoutingApi = {
  getAnswerBehavior: () =>
    requestJson<AnswerBehaviorStatus>("/api/v1/admin/answer-behavior"),
  updateAnswerBehavior: (input: AnswerBehaviorUpdateInput) =>
    requestJson<AnswerBehaviorStatus>("/api/v1/admin/answer-behavior", {
      method: "PUT",
      body: JSON.stringify({
        custom_guidance: input.customGuidance,
        expected_revision: input.expectedRevision,
        idempotency_key: clientRequestId("answer-behavior"),
      }),
    }),
  listProviderConnections: () =>
    requestJson<ProviderConnectionListResult>(
      "/api/v1/admin/config/provider-connections",
    ),
  createProviderConnection: (
    input: ProviderConnectionCreateInput,
    idempotencyKey: string,
  ) =>
    requestJson<ProviderConnectionStatus>(
      "/api/v1/admin/config/provider-connections",
      {
        method: "POST",
        body: JSON.stringify({
          display_name: input.displayName,
          provider_type: input.providerType,
          endpoint_url: input.endpointUrl,
          api_version: input.apiVersion,
          api_key: input.apiKey,
          idempotency_key: idempotencyKey,
        }),
      },
    ),
  updateProviderConnection: (input: ProviderConnectionUpdateInput) => {
    const body: Record<string, unknown> = {
      expected_revision: input.expectedRevision,
      idempotency_key: clientRequestId(`provider-connection-update-${input.connectionId}`),
    };
    if (input.displayName !== undefined) body.display_name = input.displayName;
    if (input.endpointUrl !== undefined) body.endpoint_url = input.endpointUrl;
    if (input.apiVersion !== undefined) body.api_version = input.apiVersion;
    if (input.apiKey) body.api_key = input.apiKey;
    if (input.enabled !== undefined) body.enabled = input.enabled;
    return requestJson<ProviderConnectionStatus>(
      `/api/v1/admin/config/provider-connections/${input.connectionId}`,
      { method: "PATCH", body: JSON.stringify(body) },
    );
  },
  testProviderConnection: (connectionId: string, expectedRevision: number) =>
    requestJson<ProviderConnectionTestResult>(
      `/api/v1/admin/config/provider-connections/${connectionId}/test`,
      {
        method: "POST",
        body: JSON.stringify({
          expected_revision: expectedRevision,
          idempotency_key: clientRequestId(`provider-connection-test-${connectionId}`),
        }),
      },
    ),
  listAvailableModels: (connectionId: string, signal?: AbortSignal) =>
    requestJson<AvailableModelsResult>(
      `/api/v1/admin/config/provider-connections/${connectionId}/available-models`,
      { signal },
    ),
  listModelRoutes: () =>
    requestJson<ModelRouteListResult>("/api/v1/admin/config/model-routes"),
  configureModelRoute: (
    config: ModelRouteConfigInput,
    idempotencyKey: string,
  ) =>
    requestJson<ModelRouteStatus>("/api/v1/admin/config/model-routes", {
      method: "POST",
      body: JSON.stringify({
        display_name: config.displayName,
        model_name: config.modelName,
        connection_id: config.connectionId,
        enabled: config.enabled,
        supports_vision: config.supportsVision,
        runtime_policy: config.runtimePolicy,
        idempotency_key: idempotencyKey,
      }),
    }),
  updateModelRoute: (input: ModelRouteUpdateInput) => {
    const body: Record<string, unknown> = {
      expected_revision: input.expectedRevision,
      idempotency_key: clientRequestId(`model-route-update-${input.routeId}`),
    };
    if (input.displayName !== undefined) body.display_name = input.displayName;
    if (input.modelName !== undefined) body.model_name = input.modelName;
    if (input.connectionId !== undefined) body.connection_id = input.connectionId;
    if (input.enabled !== undefined) body.enabled = input.enabled;
    if (input.supportsVision !== undefined) body.supports_vision = input.supportsVision;
    body.runtime_policy = input.runtimePolicy;
    return requestJson<ModelRouteStatus>(
      `/api/v1/admin/config/model-routes/${input.routeId}`,
      { method: "PATCH", body: JSON.stringify(body) },
    );
  },
  testModelRoute: (routeId: string, expectedRevision: number) =>
    requestJson<ModelRouteStatus>(
      `/api/v1/admin/config/model-routes/${routeId}/test`,
      {
        method: "POST",
        body: JSON.stringify({
          expected_revision: expectedRevision,
          idempotency_key: clientRequestId(`model-test-${routeId}`),
        }),
      },
    ),
  setDefaultModelRoute: (
    routeId: string,
    purpose: "text" | "vision",
    expectedRevision: number,
  ) =>
    requestJson<ModelRouteStatus>(
      `/api/v1/admin/config/model-routes/${routeId}/defaults/${purpose}`,
      {
        method: "POST",
        body: JSON.stringify({
          expected_revision: expectedRevision,
          idempotency_key: clientRequestId(`model-default-${purpose}-${routeId}`),
        }),
      },
    ),
};
