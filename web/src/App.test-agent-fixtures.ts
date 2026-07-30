export const agentList = {
  agents: [
    {
      actor_id: "agent-layout-review-001",
      actor_type: "service_account",
      display_name: "Layout Review Agent",
      status: "active",
      tokens: [
        {
          token_id: "agtok-layout-review",
          token_fingerprint: "abc123def456",
          status: "active",
          created_at: "2026-07-08T00:00:00+00:00",
          revoked_at: null,
        },
      ],
      project_grants: [
        {
          grant_id: "grant-agent-layout-review-001-signal-integrity-alpha",
          project_id: "proj-signal-integrity-alpha",
          role: "viewer",
          effect: "allow",
          status: "active",
        },
      ],
    },
  ],
};

export const auditEvents = {
  events: [
    {
      event_id: "audit-0001",
      event_type: "agent_token_issued",
      actor_id: "user-admin-001",
      target_ref: "agent-token:agtok-layout-review",
      project_id: null,
      message_code: "agent.token_has_been_issued_copy_it_now", message_params: {},
      metadata: { token_fingerprint: "abc123def456" },
      created_at: "2026-07-08T00:00:00+00:00",
    },
  ],
};
