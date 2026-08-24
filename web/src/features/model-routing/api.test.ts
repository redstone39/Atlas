import { existsSync, readFileSync } from "node:fs";

import { afterEach, describe, expect, it, vi } from "vitest";

import { modelRoutingApi } from "./index";

afterEach(() => vi.unstubAllGlobals());

function successfulFetch() {
  const mock = vi.fn().mockResolvedValue({ ok: true, text: async () => "{}" });
  vi.stubGlobal("fetch", mock);
  return mock;
}

const runtimePolicy = {
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
  context_window_tokens: 16000,
  max_input_tokens_per_invocation: 8000,
  max_output_tokens_per_invocation: 2000,
  max_tool_result_tokens_per_execution: 4000,
  max_total_tokens_per_conversation: 20000,
};

describe("provider connection and model routing API boundary", () => {
  it("reads and revision-updates the global Answer behavior contract", async () => {
    const fetchMock = successfulFetch();

    await modelRoutingApi.getAnswerBehavior();
    await modelRoutingApi.updateAnswerBehavior({
      customGuidance: "Prefer concise comparison tables.",
      expectedRevision: 4,
    });
    await modelRoutingApi.updateAnswerBehavior({
      customGuidance: null,
      expectedRevision: 5,
    });

    const requests = fetchMock.mock.calls.map(([path, init = {}]) => ({
      path,
      method: init.method,
      body: init.body ? JSON.parse(String(init.body)) : null,
    }));
    expect(requests).toEqual([
      {
        path: "/api/v1/admin/answer-behavior",
        method: undefined,
        body: null,
      },
      {
        path: "/api/v1/admin/answer-behavior",
        method: "PUT",
        body: {
          custom_guidance: "Prefer concise comparison tables.",
          expected_revision: 4,
          idempotency_key: expect.stringMatching(/^answer-behavior-/),
        },
      },
      {
        path: "/api/v1/admin/answer-behavior",
        method: "PUT",
        body: {
          custom_guidance: null,
          expected_revision: 5,
          idempotency_key: expect.stringMatching(/^answer-behavior-/),
        },
      },
    ]);
  });

  it("uses connection-owned credentials and never sends removed route secret fields", async () => {
    const fetchMock = successfulFetch();
    await modelRoutingApi.listProviderConnections();
    await modelRoutingApi.createProviderConnection({
      displayName: "Connection A",
      providerType: "azure_openai",
      endpointUrl: "https://example.openai.azure.com",
      apiVersion: "2024-10-21",
      apiKey: "secret-canary",
    }, "provider-create-a");
    await modelRoutingApi.updateProviderConnection({
      connectionId: "connection-a",
      displayName: "Connection A updated",
      apiVersion: "2024-12-01-preview",
      apiKey: "",
      expectedRevision: 3,
    });
    await modelRoutingApi.testProviderConnection("connection-a", 4);
    await modelRoutingApi.listAvailableModels("connection-a");
    await modelRoutingApi.listModelRoutes();
    await modelRoutingApi.configureModelRoute({
      displayName: "Route A",
      modelName: "deployment-a",
      connectionId: "connection-a",
      enabled: true,
      supportsVision: true,
      runtimePolicy,
    }, "route-create-a");
    await modelRoutingApi.updateModelRoute({
      routeId: "route-a",
      displayName: "Route A updated",
      supportsVision: false,
      runtimePolicy: { ...runtimePolicy, max_total_tokens_per_conversation: 24000 },
      expectedRevision: 2,
    });
    await modelRoutingApi.testModelRoute("route-a", 3);
    await modelRoutingApi.setDefaultModelRoute("route-a", "text", 4);
    await modelRoutingApi.setDefaultModelRoute("route-a", "vision", 5);

    const requests = fetchMock.mock.calls.map(([path, init = {}]) => ({
      path,
      method: init.method,
      body: init.body ? JSON.parse(String(init.body)) : null,
    }));
    expect(requests).toEqual([
      { path: "/api/v1/admin/config/provider-connections", method: undefined, body: null },
      {
        path: "/api/v1/admin/config/provider-connections",
        method: "POST",
        body: {
          display_name: "Connection A",
          provider_type: "azure_openai",
          endpoint_url: "https://example.openai.azure.com",
          api_version: "2024-10-21",
          api_key: "secret-canary",
          idempotency_key: "provider-create-a",
        },
      },
      {
        path: "/api/v1/admin/config/provider-connections/connection-a",
        method: "PATCH",
        body: {
          display_name: "Connection A updated",
          api_version: "2024-12-01-preview",
          expected_revision: 3,
          idempotency_key: expect.stringMatching(/^provider-connection-update-connection-a-/),
        },
      },
      {
        path: "/api/v1/admin/config/provider-connections/connection-a/test",
        method: "POST",
        body: {
          expected_revision: 4,
          idempotency_key: expect.stringMatching(/^provider-connection-test-connection-a-/),
        },
      },
      {
        path: "/api/v1/admin/config/provider-connections/connection-a/available-models",
        method: undefined,
        body: null,
      },
      { path: "/api/v1/admin/config/model-routes", method: undefined, body: null },
      {
        path: "/api/v1/admin/config/model-routes",
        method: "POST",
        body: {
          display_name: "Route A",
          model_name: "deployment-a",
          connection_id: "connection-a",
          enabled: true,
          supports_vision: true,
          runtime_policy: runtimePolicy,
          idempotency_key: "route-create-a",
        },
      },
      {
        path: "/api/v1/admin/config/model-routes/route-a",
        method: "PATCH",
        body: {
          display_name: "Route A updated",
          supports_vision: false,
          runtime_policy: { ...runtimePolicy, max_total_tokens_per_conversation: 24000 },
          expected_revision: 2,
          idempotency_key: expect.stringMatching(/^model-route-update-route-a-/),
        },
      },
      {
        path: "/api/v1/admin/config/model-routes/route-a/test",
        method: "POST",
        body: {
          expected_revision: 3,
          idempotency_key: expect.stringMatching(/^model-test-route-a-/),
        },
      },
      {
        path: "/api/v1/admin/config/model-routes/route-a/defaults/text",
        method: "POST",
        body: {
          expected_revision: 4,
          idempotency_key: expect.stringMatching(/^model-default-text-route-a-/),
        },
      },
      {
        path: "/api/v1/admin/config/model-routes/route-a/defaults/vision",
        method: "POST",
        body: {
          expected_revision: 5,
          idempotency_key: expect.stringMatching(/^model-default-vision-route-a-/),
        },
      },
    ]);

    const routeBodies = requests
      .filter((request) => String(request.path).includes("model-routes"))
      .map((request) => JSON.stringify(request.body));
    expect(routeBodies.join(" ")).not.toContain("secret_ref");
    expect(routeBodies.join(" ")).not.toContain("endpoint_url");
  });

  it("omits Azure api_version for non-Azure provider profiles", async () => {
    const fetchMock = successfulFetch();

    await modelRoutingApi.createProviderConnection({
      displayName: "Anthropic",
      providerType: "anthropic",
      endpointUrl: "https://api.anthropic.com",
      apiKey: "secret-canary",
    }, "provider-create-anthropic");

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body).toEqual({
      display_name: "Anthropic",
      provider_type: "anthropic",
      endpoint_url: "https://api.anthropic.com",
      api_key: "secret-canary",
      idempotency_key: "provider-create-anthropic",
    });
    expect(body).not.toHaveProperty("api_version");
  });

  it("keeps page/registry on the public contract without root facades", () => {
    const feature = readFileSync("src/features/model-routing/ModelRoutingFeature.tsx", "utf8");
    expect(feature).not.toContain('from "../../api"');
    expect(feature).not.toContain('from "../../types"');
    expect(feature).not.toContain("secretRef");
    expect(
      readFileSync("src/features/model-routing/ConnectionsTab.tsx", "utf8"),
    ).toContain("sm:flex-row");
    expect(
      readFileSync("src/features/model-routing/ModelsTab.tsx", "utf8"),
    ).toContain("overflow-x-auto");
    expect(feature).not.toContain("deleteProviderConnection");
    expect(feature).not.toContain("deleteModelRoute");
    const page = readFileSync("src/components/pages/AdminModelsPage.tsx", "utf8");
    expect(page).toContain('from "../../features/model-routing/index"');
    expect(page).not.toContain("useState");
    expect(existsSync("src/api.ts")).toBe(false);
    expect(existsSync("src/types.ts")).toBe(false);
    const registry = JSON.parse(readFileSync("../architecture-boundaries.json", "utf8"));
    const owner = registry.owners.find((item: { id: string }) => item.id === "frontend_features");
    expect(owner.public_contracts).toContain(
      "web/src/features/model-routing/index.ts",
    );
  });
});
