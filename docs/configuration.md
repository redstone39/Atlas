# Configuration

Atlas reads process configuration at startup and stores Provider/model routing
and LDAP/Active Directory connection metadata through System Admin. Environment
variables are deployment inputs, not a mutable global configuration database.

## First System Admin

Atlas does not seed an administrator from environment variables. While Identity
contains no users, `GET /api/v1/auth/first-admin` reports that the one-time claim
is available. The browser redirects from `/login` to `/setup`, where the
operator supplies a display name, email, and password. The Identity owner
serializes concurrent claims; exactly one request creates the first System
Admin and session. After any user exists, the claim endpoint remains
unavailable. Restarting services does not reopen it.

The public Compose stack initializes credential-encryption and Notes
collaboration secrets into dedicated persistent volumes when explicit
environment overrides are absent. Existing secret files are reused across
restart. Environment values remain supported as deliberate operator overrides;
they are deployment inputs and must not be committed.

## Notes collaboration

| Variable | Required | Meaning |
|---|---:|---|
| `ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET` | Yes | API-to-carrier transport authentication |
| `ATLAS_NOTES_COLLABORATION_TICKET_SECRET` | Yes | API-only collaboration-ticket signing |
| `ATLAS_NOTES_COLLABORATION_PUBLIC_URL` | No | Browser-reachable WebSocket URL; defaults to loopback |
| `ATLAS_PRODUCTION_COLLABORATION_PORT` | No | Loopback host port; defaults to `8015` |

Generate the two required values independently with `openssl rand -base64 32`.
They must differ. The ticket secret is passed only to the API; the transport
secret is shared only by the API and collaboration carrier. Retain both across
normal restarts. Changing either value invalidates current connections; restart
the API and carrier together. For a browser on another host, provide a secured
reachable `ws://` or `wss://` public URL and operate its TLS/reverse proxy
outside this repository.

## Provider credential encryption

| Variable | Meaning |
|---|---|
| `ATLAS_CREDENTIAL_MASTER_KEY` | Base64-encoded 32-byte active AES-GCM key |
| `ATLAS_CREDENTIAL_MASTER_KEY_ID` | Stable identifier for the active key |
| `ATLAS_CREDENTIAL_MASTER_KEYRING` | Optional JSON map of retained keys used to read older ciphertext |

Generate a local evaluation key:

```sh
openssl rand -base64 32
```

Keep the active key and key ID stable for as long as encrypted Provider or
directory credentials must remain readable. Losing all matching keys makes
Provider API keys, directory bind passwords, and custom CA material
unrecoverable. Enter those secrets through System Admin; do not add
Provider- or directory-specific credentials to `.env`.

## LDAP and Active Directory connections

System Admin may create `ldap` or `active_directory` connections with an
explicit `ldaps`, `start_tls`, or `plain` transport, a bind identity, search
bases/filters, attribute mappings, and a priority. `plain` is a deliberately
selected plaintext mode for a trusted evaluation network; the UI warns before
save, custom CA input is unavailable, and TLS failure never falls back to it.
Bind passwords and optional TLS custom CA material are encrypted with the
credential master key using secret-kind-specific authenticated data. They are
write-only: list, create, update, test, search, scoped import, and
profile-refresh responses never return plaintext or ciphertext.

Configure a readable `ATLAS_CREDENTIAL_MASTER_KEY`, its key ID, and any retained
keyring entries before enabling a directory connection. Missing or unreadable
key material makes directory secret use unavailable and login through the
selected source fails closed. Atlas provides no per-connection environment or
automatic fallback credential path.

Directory integration is unconfigured by default. Local login remains
available. Imported identities must be created through the System Admin search
and import flow; Atlas does not run a scheduled directory synchronization.
Atlas account activity, roles, grants, ACLs, and sessions remain authoritative
after import.

## Provider connections

System Admin stores one of three connection profiles:

- `openai_compatible`: an HTTPS base URL, or loopback HTTP for local evaluation;
- `azure_openai`: a pathless Azure resource root plus a required API protocol
  version such as `<api-version>`;
- `anthropic`: the fixed `https://api.anthropic.com` endpoint.

The endpoint, profile, and nullable Azure API version are persisted with the
connection. The API key is encrypted with the credential master key. Each
attempt passes these values directly to the in-process LiteLLM carrier; no
Provider-specific environment credential or global LiteLLM setting is used.
Route-less execution uses only the eligible route explicitly marked as default.
Changing or recovering a connection does not implicitly select another route.

Text and vision defaults are independent persisted selections. Setting the
vision default requires an enabled, successfully tested route whose selected
model advertises vision capability. Disabling a connection or route fails
closed until the affected default is changed explicitly; Atlas does not choose
another eligible route automatically.

## Conversation learning admission

Conversation learning is enabled at the fresh development baseline. Disabling
it affects new Review and Learner reconciliation work only; it does not
retroactively admit earlier conversations. Concurrent edits use the displayed
revision. A conflict reloads the current persisted value before the operator
retries.

## Reasoning route policy

The UI labels `standard` as **General** and `deep` as **In-depth**. Stored route
policy and runtime values remain `standard|deep`.

Model routes carry the bounded runtime policy used by both standard and deep
turns. `max_reasoning_revision_cycles` accepts `0..3` and defaults to `2`.
`max_schema_retries_per_turn` accepts `1..3`; its selected value is fixed in
each accepted execution and shared by every structured-output repair stage.
Provider capacity must satisfy:

```text
max_provider_invocations >= max_tool_invocations
  + 4 * max_reasoning_revision_cycles
  + 6
```

This reserves capacity for planning, candidate generation, provisional evidence
assessment, evaluation, bounded revision, and terminal governance. System Admin
keeps these limits in the route's technical details; changing them affects new
executions only.

The initial structured-output attempt does not consume the schema retry budget.
A decode, schema, or stage-local structured-output contract failure must claim
the durable counter before a repair invocation is created. Transport, timeout,
authentication, rate-limit, refusal, routing, authorization, deadline, and
physical-limit failures do not consume this budget.

These limits have distinct scopes. `max_provider_invocations` is the hard limit
for model actions within one execution. `max_total_tokens_per_conversation` is
the conversation-level soft cost limit and aggregates completed normalized
Provider usage from context preparation, answer generation, evaluation, and
evidence assessment. Atlas checks that total before accepting a new turn; an
already accepted turn may cross the threshold, and the following new turn is
then rejected. Provider-completed attempts retain their observed usage even
when Atlas rejects the structured output and performs a bounded repair.

Runtime policy schema v8 also provides:

- `max_model_visible_items_per_turn`: one deduplicated limit shared by
  evidence, page, visual, and navigation handles;
- `max_retrieval_repairs`: the bounded retrieval contract-repair count;
- `max_selected_anchor_pages_per_round`: the maximum expand anchors accepted
  in one retrieval round;
- `tool_execution_timeout_seconds`: the per-tool timeout, which must not
  exceed the overall turn timeout.

Route changes affect only newly accepted executions. Admin diagnostics use the
policy snapshot stored with the execution and do not reinterpret historical
usage with the current route.

## Runtime inputs

- PostgreSQL: `ATLAS_PRODUCTION_DATABASE_URL`
- Redis/Qdrant: `ATLAS_REDIS_URL`, `ATLAS_QDRANT_URL`,
  `ATLAS_QDRANT_TIMEOUT_SECONDS`
- Artifacts: `ATLAS_ARTIFACT_TARGET_CONFIG`,
  `ATLAS_ARTIFACT_ALLOWED_PARENTS`
- Processing: `ATLAS_PLUGIN_RUNNER_URL`, `ATLAS_OFFICE_RENDERER_URL`,
  `ATLAS_PROCESSING_PLUGIN_ARTIFACT_ROOT`, `ATLAS_PDF_MAX_PAGES`,
  `ATLAS_DOCLING_LAYOUT_TIMEOUT_SECONDS`
- Plugin trust: `ATLAS_PLUGIN_TRUSTED_KEYS_JSON`,
  `ATLAS_ALLOW_UNSIGNED_PLUGINS`
- Notes collaboration: `ATLAS_NOTES_COLLABORATION_INTERNAL_URL`,
  `ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET`,
  `ATLAS_NOTES_COLLABORATION_TICKET_SECRET`,
  `ATLAS_NOTES_COLLABORATION_PUBLIC_URL`
- Offline caches: `ATLAS_FASTEMBED_CACHE`, `TIKTOKEN_CACHE_DIR`,
  `ATLAS_EMBEDDING_OFFLINE`. Compose initializes the pinned embedding cache from
  the separate model image before API and processing/indexing consumers start;
  runtime network download fallback is unsupported.
- Process proxy for API and `celery-processing`: uppercase `HTTP_PROXY`,
  `HTTPS_PROXY`, and `NO_PROXY`. The Portainer compositions prepend
  `::1,127.0.0.1,localhost,postgres,redis,qdrant,plugin-runner,office-renderer,notes-collaboration,api,web`
  to `NO_PROXY`; lowercase proxy inputs are ignored.

The Compose files provide local defaults for internal service addresses.
`/api/v1/ops/readiness` reports safe blockers without returning credentials or
raw Provider payloads.

## Local files

A fresh local Compose evaluation needs no `.env` file. Use
`infra/.env.example` only as a reference when deliberately supplying deployment
overrides. The generated credential and Notes secrets remain in dedicated
Compose volumes across normal restarts. Never commit `.env`, private keys,
Provider keys, SMB credentials, database dumps, uploaded documents, or
generated offline bundles.
