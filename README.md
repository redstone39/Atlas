# Atlas

Atlas is a self-hosted knowledge workspace for governed document access,
evidence-grounded AI conversations, and collaborative notes.

**Status:** technical-evaluation snapshot · `resettable_development` data
lifecycle · local Docker Compose · **not Release Ready or Internet Ready**

Atlas combines document intake and processing, Project- and Team-scoped
knowledge, General and In-depth conversations, evidence review, collaborative
Notes, local or LDAP/Active Directory identity, administration, and auditable
access controls in one deployment.

This repository contains a runnable standalone public snapshot of the Atlas
runtime. It is published for source transparency, technical evaluation, and
self-hosted demonstration under the Apache License, Version 2.0. The public tree
is maintained independently from private development and does not represent a
hosted service, security certification, production release, or support
commitment.

## What Atlas is for

Atlas is designed for people who want to:

- run a complete knowledge workspace locally with Docker Compose;
- separate document and Note access by Project or Team;
- compare a normal General answer flow with a bounded In-depth review flow;
- inspect protected evidence and auditable execution state instead of treating
  a model response as an opaque chat result;
- study a source-visible implementation of governed retrieval, collaborative
  Notes, identity integration, processing plugins, and agentic engineering.

The current public snapshot has these boundaries:

- it is self-hosted rather than a hosted or turnkey service;
- it is for loopback-only technical evaluation, not direct public-Internet
  exposure;
- production data migration, upgrade compatibility, backup, high availability,
  automatic failover, and an operations SLA are not provided;
- Provider credentials are not bundled, and model answers are not
  deterministic;
- community participation currently happens through Issues, Discussions,
  documentation feedback, and deployment experiences rather than external pull
  requests.

These boundaries are open to discussion. If Atlas is missing something
important for your use case, share the scenario, expected outcome, and relevant
environment details through
[Issues](https://github.com/redstone39/atlas-public/issues) or
[Discussions](https://github.com/redstone39/atlas-public/discussions).

## Why Atlas

- **Governed knowledge:** assign documents and Notes to Projects or Teams, then
  recheck current access before protected content is read or changed.
- **Traceable conversations:** ask within all accessible knowledge or a frozen
  scope selection, and inspect the protected evidence made available with an
  answer.
- **Collaborative work:** create scope-bound Notes with block editing, revisions,
  savepoints, restore, activity, and protected attachments.
- **Operator control:** configure identity, model routes, processing plugins,
  Skill slots, audit inspection, and system status from System Admin.

## How it works

```text
Documents and Notes
        ↓
Governed processing and scoped access
        ↓
Project / Team knowledge
        ↓
General (standard) or In-depth (deep) conversation
        ↓
Answer, protected evidence, and auditable execution state
```

## Product preview

![A fresh Atlas workspace after the first local sign-in](docs/assets/atlas-fresh-workspace.png)

*A fresh local evaluation workspace before knowledge or conversations have been
added. Provider credentials and sample documents are not included in this public
snapshot.*

## Local quick start

You need Git, Docker Engine with Docker Compose v2, enough memory and disk for
the complete stack and pinned embedding-model cache, and a fresh disposable
evaluation environment.

Clone Atlas and create the local configuration:

```sh
git clone https://github.com/redstone39/atlas-public.git
cd atlas-public
cp infra/.env.example infra/.env
```

Set these four values in `infra/.env`:

```dotenv
ATLAS_BOOTSTRAP_ADMIN_EMAIL=you@example.com
ATLAS_BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-unique-password
ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET=replace-with-random-value-one
ATLAS_NOTES_COLLABORATION_TICKET_SECRET=replace-with-random-value-two
```

Generate the two Notes secrets independently with `openssl rand -base64 32`;
they must be non-empty and different. Start the stack:

```sh
cd infra
docker compose -f docker-compose.p1.yml up --build -d
docker compose -f docker-compose.p1.yml ps
curl -fsS http://127.0.0.1:8012/api/v1/ops/health
curl -fsS http://127.0.0.1:8012/api/v1/ops/readiness
```

Open <http://127.0.0.1:5184/login> and sign in with the bootstrap credentials.

Read [Local Docker Compose deployment](docs/deployment/local.md) before restart,
replacement, recovery, or reset. Its `down -v` path permanently deletes the
Compose project's local application data and is only for the documented
`resettable_development` lifecycle. [Configuration](docs/configuration.md) owns
the exact bootstrap, secret, Provider, and runtime input rules.

## Your first Atlas workflow

The first sign-in proves that the local application is running. To exercise the
AI knowledge journey:

1. Configure the credential master key, then add and test a Provider connection
   and model route under **Models**. Provider credentials are required for model
   answers and are not bundled.
2. Create a Project or Team and add a non-sensitive document that you are
   authorized to use for evaluation.
3. Wait for the document's processing job to reach a terminal state.
4. Start a conversation and keep **All accessible knowledge** or select an
   allowed Project or Team scope.
5. Choose **General** (`standard`) or **In-depth** (`deep`) and ask a question
   about the document.
6. Inspect the answer and its available protected evidence. Evidence review is
   not a truth or formal citation guarantee.

No sample knowledge, private test documents, Provider keys, or expected model
answers are included in the public snapshot.

## Status and supported use

| Evaluation path | Current status |
|---|---|
| Fresh local Docker Compose deployment | Supported for technical evaluation |
| Local identity, governed document processing, scoped conversations, and Notes | Supported in the resettable evaluation lifecycle |
| Provider connections and model routes | Configurable by System Admin; credentials are not included |
| LDAP / Active Directory | Shipped unconfigured; live interoperability is not verified here |
| Portainer with SMB | Guides and audits exist; real-environment operation is not verified |
| Public Internet deployment | Not supported |
| In-place application-data migration between snapshots | Not supported |
| High availability, automatic failover, and shared-state multi-deployment | Not supported |
| Managed TLS, backup, monitoring, capacity, and abuse protection | Not provided |

Keep the default Compose ports bound to loopback. Replacing a snapshot requires
a fresh application data set; identities, conversations, routing configuration,
processing state, and other application data are not migrated.

## Architecture at a glance

The Web UI and FastAPI application coordinate PostgreSQL-owned business state,
Redis-carried background work, Qdrant semantic candidates, governed artifact
storage, processing plugins, an isolated Office renderer, and a request/event-
only Notes collaboration carrier. PostgreSQL remains authoritative for access,
audit, conversations, routing, processing, and durable Notes content.

The pinned offline embedding cache is initialized before API and worker
consumers start. Runtime model downloads are disabled. See the
[documentation index](docs/README.md) for the exact authority, access, failure,
reasoning, configuration, deployment, and lifecycle contracts.

## Administration

![Atlas System Admin settings in a fresh local evaluation deployment](docs/assets/atlas-admin-settings.png)

*System Admin exposes identity, Projects and Teams, the document library,
Models, Skill slots, processing plugins, agents, audit, system status, language,
theme, and Notes checkpoint settings. The pictured account is local evaluation
data.*

## How Atlas is built

Atlas is also the reference workload for a harness-engineered development
methodology. Product intent, requirements, architecture, risk, and final
acceptance remain human-owned; bounded coding agents work against repository
contracts, tests, audits, and reproducible operator journeys. Read
[Development methodology](docs/development-methodology.md) for the exact public
boundary and evidence exposed by this snapshot.

## Continue exploring

The [Atlas documentation index](docs/README.md) is the next entry point after
this README:

| Your question | Start here |
|---|---|
| How does Atlas work and who owns each decision? | [Architecture and trust boundaries](docs/architecture.md) |
| How do bootstrap, secrets, Providers, directory identity, and runtime limits work? | [Configuration](docs/configuration.md) |
| How do I operate or reset a fresh local deployment? | [Local deployment](docs/deployment/local.md) |
| How do I verify the public source tree? | [Verification](docs/verification.md) |
| How can I report feedback or discuss Atlas? | [Community and feedback](CONTRIBUTING.md) |

## Community

Atlas welcomes reproducible bug reports, documentation feedback, self-hosting
and deployment experiences, compatibility reports, questions, use cases, and
feature discussions.

Use [GitHub Issues](https://github.com/redstone39/atlas-public/issues) for
specific defects and reports. Use
[GitHub Discussions](https://github.com/redstone39/atlas-public/discussions) for
questions, experiences, architecture discussion, and broader ideas.

External pull requests and unsolicited patches are not accepted at this time,
and there is no announced timeline for changing that boundary. Do not publish
vulnerabilities, credentials, private documents, exploit details, or unredacted
logs; follow the private-reporting direction in [SECURITY.md](SECURITY.md). No
support SLA or implementation timeline is offered.

## License

Atlas source in this repository is licensed under Apache License 2.0. See
[LICENSE](LICENSE), [NOTICE](NOTICE), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
