# Architecture and trust boundaries

Atlas is a single-deployment, self-hosted document knowledge workspace. The
supported topology contains Web, API, a request/event-only Notes collaboration
carrier, workers, PostgreSQL, Redis, Qdrant, processing plugins, an Office
renderer, governed artifact storage, and a one-shot embedding-model cache
initializer.

## User journey

1. A local or imported directory user authenticates; Atlas remains authoritative
   for account activity, roles, grants, ACLs, and sessions.
2. An authorized Project or Team uploader assigns a scope and uploads one or
   more documents.
3. Document intake and processing create a current, traceable generation; the
   Web consumer refreshes active jobs until terminal state.
4. A scope-authorized member enters the canonical Project/Team Knowledge or
   Notes route. Direct routes check current server scope before content fetch.
5. Notes clients obtain a short-lived API ticket and connect to the collaboration
   carrier. The carrier revalidates access and persists every accepted revision,
   savepoint, restore, and attachment decision through the API/PostgreSQL owner.
6. A user creates a Workspace conversation with default-all knowledge or an
   immutable Team/Project scope selection, then submits a `standard` or `deep`
   turn.
7. The runtime intersects the frozen selection with current authorization,
   builds immutable context references, and invokes retrieval and answer tools
   under bounded budgets. Deep turns additionally run bounded planning,
   evaluation, and revision.
8. A terminal transaction publishes the answer, runtime events, safe reasoning
   progress, evidence review status, and protected evidence references.
9. Every later protected read recomputes current authorization and checks exact
   artifact lineage.

## Authority

- PostgreSQL owner repositories are authoritative for local and imported
  identities, directory configuration, projects, grants, documents, processing,
  conversations, turns, routing, audit, and terminal state.
- PostgreSQL is also authoritative for scope-bound Notes, immutable revisions
  and savepoints, collaboration epochs, restore commits, settings, and
  attachment metadata. The WebSocket carrier keeps only reconstructible
  in-memory room/timer state and has no durable volume or ACL authority.
- Local or SMB storage owns artifact bytes, but it does not grant access or
  define business state.
- Qdrant supplies semantic candidates. It is not an authorization or lineage
  authority.
- Redis carries background work. Queue delivery is not durable business
  authority.
- `turn_execution` coordinates work; `turn_runtime` owns execution state,
  leases, budgets, events, and terminal transitions.
- The embedding contract owns its model name, revision, allowlist, and content
  digest. `embedding-model-init` verifies and initializes the shared offline
  cache; its image and volume are carriers, not a second model authority.

Architecture ownership and dependency direction are executable in
`architecture-boundaries.json` and checked by
`infra/scripts/audit_architecture_boundaries`.

## Access, evidence, and failure behavior

- Document and conversation access is recalculated from current direct and
  transitive grants on every request.
- Conversation ownership never grants document access.
- A conversation's optional Team/Project selection is immutable. Fresh and
  retry execution intersects it with current ACLs; an empty result remains empty
  rather than reverting to default-all.
- Direct Team Admin or exact-scope Project Admin authority may bypass a
  document's member-download flag only for that document's exact owner scope,
  and the same capability is rechecked at terminal byte I/O.
- Knowledge and Notes direct routes preflight the exact current Project/Team
  scope. Notes connection tickets are short-lived; every accepted sync,
  reconnect, restore, and attachment read rechecks current authorization and
  the collaboration epoch.
- Evidence preview requires current authorization and exact immutable lineage.
- An answer's `evidence_aligned` or `questionable` status is a soft comparison
  with model-declared evidence, not a truth guarantee or formal citation
  verification.
- Formal citation bindings are separate from declared evidence.
- Deleting a Workspace conversation is an owner-only `active -> archived`
  transition. Archived conversations are hidden from the member Workspace but
  retained for System Admin audit; Atlas does not physically delete them.
- Invalid authority, lineage, lease, configuration, or artifact checks fail
  closed and do not publish a fabricated successful result.
- Protected previews do not provide public URLs, persistent viewer tokens, or
  cross-page document navigation.

## Identity directory integration

Identity Access checks a unique local email account first. Only when none exists
may it select the first-priority enabled LDAP or Active Directory connection
with exactly one imported external-identity match. Selection is final for that
attempt: transport failure, disabled directory principal, invalid password,
alias conflict, inactive Atlas account, or concurrent deactivation fails closed
without trying another source.

Directory transport is an explicit `ldaps|start_tls|plain` setting. Plaintext is
never inferred from port or TLS failure. Connection metadata and external
identities are durable PostgreSQL state. Bind passwords and optional TLS custom
CA material use the existing AES-256-GCM credential keyring with kind-specific
authenticated data. Secret values are write-only and never returned. Missing or
unreadable key material makes directory secret operations unavailable; there is
no per-connection environment fallback or scheduled directory sync.

## Provider and model routing

The model-routing owner persists Provider connections, encrypted credentials,
model routes, explicit default selection, readiness, and attempt state.
Connections use the closed profiles `openai_compatible`, `azure_openai`, and
`anthropic`; Azure additionally persists its required API protocol version.

LiteLLM 1.95.0 is an in-process completion carrier, not a routing authority.
Each attempt supplies the stored key, endpoint, and applicable Azure version
directly to one synchronous completion call with SDK retries disabled. Atlas
does not use LiteLLM Proxy, Router, global credentials, environment credential
fallback, or automatic route fallback. A route-less execution fails closed
unless the persisted explicit default is currently eligible.

## Reasoning execution

- A conversation records its default `standard|deep` mode. Each accepted
  execution keeps an immutable mode; retry uses the source execution's mode.
- Deep execution uses the existing runtime lease, fence, budgets, tools,
  retrieval, governance, and terminal transaction. It does not add a background
  reasoning service or a second answer authority.
- Workspace receives only allowlisted phase, status, cycle, and message fields.
  Plans, drafts, prompts, Provider payloads, and Provider reasoning are not
  projected to members.
- System Admin may inspect a bounded structured trace containing Atlas-owned
  plan, evaluation, revision, termination, and digest metadata. Process scores
  measure completion of configured steps, not factual correctness.
- Provisional evidence checks and the Process Evaluator remain independent.
  Runtime deterministically combines their correction requirements and can
  require another revision or mark the final answer questionable, but they do
  not create formal citation authority.
- A correction-limit answer remains questionable even if its final declared
  evidence check aligns. It remains visible to the member and System Admin, but
  its assistant text and direct document dependencies are excluded from later
  model context.
- Context summary, resolver, rewrite, planner, replanner, answer, Process
  Evaluator, and provisional evidence checks share one execution-fixed schema
  retry budget. Every repair must first claim the durable fenced counter;
  exhausted budgets preserve each stage's fail-closed or unavailable behavior.
- Context Summary V4 keeps historical user context separate from assistant
  pending-verification context. Assistant history is dialogue context only and
  cannot grant evidence authority.
- Evidence, page, visual, and navigation handles share one deduplicated
  execution-fixed model-visible item budget. System Admin diagnostics report
  the exact execution snapshot rather than the current route configuration.

## Product surfaces

- Knowledge: canonical Project/Team and Workspace routes, authorized multi-file
  upload, current processing status, and authorized original content.
- Notes: scope-bound directories, categories, collaborative block editing,
  activity/revisions, savepoints, body-only restore, settings, and protected
  attachments.
- Workspace: conversation routes, durable execution status, answers, soft
  evidence review, and protected page evidence.
- System Admin: users, teams, projects, Provider/model routes with independent
  text/vision defaults, explicit LDAP/Active Directory transport and scoped
  imports, Notes settings, plugins, agents/tokens, safe audit events, and
  conversation runtime inspection.
- Agent query management exists, but `POST /api/v1/agent/queries` currently
  returns `501 feature_deferred`.

## Data lifecycle

This snapshot supports `resettable_development`. It has one Alembic baseline
with `down_revision = None`. Existing application data is not migrated between
software versions. Replacing a snapshot requires a fresh database and fresh
Compose volumes; it is not an in-place software upgrade. A second deployment
must not share PostgreSQL or artifact storage with the first.

Operator-owned responsibilities include TLS, host hardening, mounts, capacity,
monitoring, backups, service lifecycle, and physical recovery. Atlas owns safe
application I/O, known-failure detection, readiness, fencing, and application
crash reconciliation within the supported single deployment.
