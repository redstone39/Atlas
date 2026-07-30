# Architecture and trust boundaries

Atlas is a single-deployment, self-hosted document knowledge workspace. The
supported topology contains Web, API, workers, PostgreSQL, Redis, Qdrant,
processing plugins, an Office renderer, and governed artifact storage.

## User journey

1. An administrator uploads a document and assigns its access scope.
2. Document intake and processing create a current, traceable generation.
3. A user submits a turn in a Workspace conversation.
4. The runtime resolves current authorization, builds immutable context
   references, and invokes retrieval and answer tools under bounded budgets.
5. A terminal transaction publishes the answer, runtime events, evidence review
   status, and protected evidence references.
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
- Invalid authority, lineage, lease, configuration, or artifact checks fail
  closed and do not publish a fabricated successful result.
- Protected previews do not provide public URLs, persistent viewer tokens, or
  cross-page document navigation.

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
software versions. A second deployment must not share PostgreSQL or artifact
storage with the first.

Operator-owned responsibilities include TLS, host hardening, mounts, capacity,
monitoring, backups, service lifecycle, and physical recovery. Atlas owns safe
application I/O, known-failure detection, readiness, fencing, and application
crash reconciliation within the supported single deployment.
