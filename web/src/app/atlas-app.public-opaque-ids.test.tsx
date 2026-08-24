import { afterEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import { modelRoutingApi } from "../features/model-routing/api";
import { projectGovernanceApi } from "../features/project-governance/api";
import { retainClientRequestId } from "../shared/ids";

const response = (body: object) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

function requestBodies(fetchMock: Mock) {
  return fetchMock.mock.calls.map(([, init]) =>
    JSON.parse(String((init as RequestInit).body)),
  );
}

describe("public opaque resource ID consumers", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends project attributes and one operation key without a caller project ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      message_code: "project.is_ready_for_membership_setup",
      message_params: {},
      request_id: "public-synthetic-project-create",
      status: "applied",
      target_ref: "project:proj-public-synthetic",
      audit_event_ref: "audit-public-synthetic-project",
    }));
    global.fetch = fetchMock;

    await projectGovernanceApi.createProject(
      "Public Synthetic Project",
      "public-synthetic-project-create",
    );

    expect(requestBodies(fetchMock)).toEqual([{
      name: "Public Synthetic Project",
      policy_profile_id: "policy-default-governed",
      idempotency_key: "public-synthetic-project-create",
    }]);
  });

  it("reuses an in-flight provider operation key and never sends a connection ID", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(response({
      connection_id: "connection-public-synthetic-owner",
      display_name: "Public Synthetic Provider",
      provider_type: "openai_compatible",
      endpoint_url: "https://provider.example.test/v1",
      api_version: null,
      credential_configured: true,
      status: "configured",
      enabled: true,
      linked_model_count: 0,
      revision: 1,
      last_verified_at: null,
      last_rotated_at: null,
      audit_event_ref: "audit-public-synthetic-provider",
      message_code: "provider.connection_created",
      message_params: {},
    })));
    global.fetch = fetchMock;
    const input = {
      displayName: "Public Synthetic Provider",
      providerType: "openai_compatible" as const,
      endpointUrl: "https://provider.example.test/v1",
      apiVersion: undefined,
      apiKey: "public-synthetic-api-key",
    };
    const operation = retainClientRequestId(
      null,
      "provider-connection-create",
      JSON.stringify(input),
    );
    const retry = retainClientRequestId(
      operation,
      "provider-connection-create",
      JSON.stringify(input),
    );

    await modelRoutingApi.createProviderConnection(input, operation.idempotencyKey);
    await modelRoutingApi.createProviderConnection(input, retry.idempotencyKey);

    expect(retry).toBe(operation);
    const bodies = requestBodies(fetchMock);
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[0].idempotency_key).toBe(operation.idempotencyKey);
    expect(bodies[0]).not.toHaveProperty("connection_id");
  });
});
