# Architecture and trust boundaries

Atlas is a single-deployment, self-hosted document knowledge workspace. The
supported topology contains Web, API, workers, PostgreSQL, Redis, Qdrant,
processing plugins, an Office renderer, and governed artifact storage.

## User journey

1. An administrator uploads a document and assigns its access scope.
2. Document intake and processing create a current, traceable generation.
3. A user submits a `standard` or `deep` turn in a Workspace conversation.
4. The runtime resolves current authorization, builds immutable context
   references, and invokes retrieval and answer tools under bounded budgets.
   Deep turns additionally run bounded planning, evaluation, and revision.
5. A terminal transaction publishes the answer, runtime events, safe reasoning
   progress, evidence review status, and protected evidence references.
6. Every later protected read recomputes current authorization and checks exact
   artifact lineage.

## Authority

- PostgreSQL owner repositories are authoritative for identity, projects,
  grants, documents, processing, conversations, turns, routing, audit, and
  terminal state.
- Local or SMB storage owns artifact bytes, but it does not grant access or
  define business state.
- Qdrant supplies semantic candidates. It is not an authorization or lineage
  authority.
- Redis carries background work. Queue delivery is not durable business
  authority.
- `turn_execution` coordinates work; `turn_runtime` owns execution state,
  leases, budgets, events, and terminal transitions.

Architecture ownership and dependency direction are executable in
`architecture-boundaries.json` and checked by
`infra/scripts/audit_architecture_boundaries`.

## Access, evidence, and failure behavior

- Document and conversation access is recalculated from current direct and
  transitive grants on every request.
- Conversation ownership never grants document access.
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

- Document Library: upload, scope, processing control, status, and authorized
  original content.
- Workspace: conversation routes, durable execution status, answers, soft
  evidence review, and protected page evidence.
- System Admin: users, teams, projects, Provider/model routes, plugins,
  agents/tokens, safe audit events, and conversation runtime inspection.
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
