# Configuration

Atlas reads process configuration at startup and stores Provider/model routing
through System Admin. Environment variables are deployment inputs, not a mutable
global configuration database.

## Bootstrap administrator

| Variable | Required | Meaning |
|---|---:|---|
| `ATLAS_BOOTSTRAP_ADMIN_EMAIL` | Empty Identity only | Initial System Admin login email |
| `ATLAS_BOOTSTRAP_ADMIN_PASSWORD` | Empty Identity only | Initial password, minimum 12 characters |

The Identity owner checks whether any user already exists while holding the
bootstrap lock. If Identity is empty, missing or invalid values reject
initialization. If any user exists, bootstrap is a no-op and the variables are
not required. Changing them never rotates, revives, or overwrites an account.

Bootstrap values are passed only to the one-shot initializer. They are not
returned in JSON or written to application tables as plaintext. Docker
administrators can inspect container configuration, so use unique temporary
values and remove them from `.env` after the first successful initialization.

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

Keep the active key and key ID stable for as long as encrypted Provider
credentials must remain readable. Losing them makes those credentials
unrecoverable. Provider API keys are entered through System Admin; do not add
Provider-specific keys to `.env`.

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

## Reasoning route policy

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
- Offline caches: `ATLAS_FASTEMBED_CACHE`, `TIKTOKEN_CACHE_DIR`,
  `ATLAS_EMBEDDING_OFFLINE`
- Process proxy for API and `celery-processing`: uppercase `HTTP_PROXY`,
  `HTTPS_PROXY`, and `NO_PROXY`. The Portainer compositions prepend
  `::1,127.0.0.1,localhost,postgres,redis,qdrant,plugin-runner,office-renderer,api,web`
  to `NO_PROXY`; lowercase proxy inputs are ignored.

The Compose files provide local defaults for internal service addresses.
`/api/v1/ops/readiness` reports safe blockers without returning credentials or
raw Provider payloads.

## Local files

Copy `infra/.env.example` to `infra/.env`. The real `.env` is ignored by Git.
Never commit `.env`, private keys, Provider keys, SMB credentials, database
dumps, uploaded documents, or generated offline bundles.
