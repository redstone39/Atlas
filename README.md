# Atlas

Atlas is a self-hosted knowledge platform for people and agents.

People use Atlas to organize governed documents, collaborate in Notes, and ask
questions with inspectable evidence. Agents use Atlas through MCP to research
currently authorized Project knowledge and read the evidence behind their
results.

**Atlas is more than a document-chat or RAG interface: it brings human and
agent access, authorization, and evidence inspection into the same knowledge
workflow.**

> **Current public snapshot:** local technical preview ·
> `resettable_development` data lifecycle · **not Release Ready or Internet
> Ready**

[Start Atlas locally](#start-atlas-locally) ·
[Read the documentation](docs/README.md) ·
[Join the discussion](https://github.com/redstone39/Atlas/discussions)

## Product preview

![A fresh Atlas workspace after leaving the guided setup](docs/assets/atlas-fresh-workspace.png)

*A fresh local evaluation workspace after completing or skipping the guided
setup. Provider credentials and sample documents are not included in this
public snapshot.*

## For people and agents

| | People | Agents |
|---|---|---|
| **Entry point** | Web workspace | MCP at exact `/mcp` |
| **Knowledge access** | Current Project- and Team-scoped access | Currently authorized Project scopes |
| **Primary work** | Documents, collaborative Notes, and General or In-depth conversations | Scope discovery, one-round research, terminal polling, and protected evidence reads |
| **Inspectable result** | Answer, execution state, and protected evidence | Immutable research packet, optional packet-bound answer, and protected evidence |

Agent access is intentionally narrower than the human workspace in this public
snapshot. Agents do not upload documents or edit Notes through MCP.

## Why Atlas

- **One knowledge platform:** People work through the Web UI while Agents use
  governed Project knowledge through MCP.
- **Current authorization:** Human knowledge routes and Agent research use
  current access rules. Protected reads recheck authorization before returning
  content.
- **Inspectable results:** Human answers and Agent research expose evidence and
  auditable execution state instead of acting like opaque model output.
- **Self-hosted workspace:** Document processing, conversations, collaborative
  Notes, identity, administration, model routing, and Agent research run in one
  deployment.

## How Atlas works

```text
People ──→ Web workspace ──→ Project / Team knowledge ──→ Answers + evidence
Agents ──→ MCP research ───→ Authorized Project knowledge ──→ Packets + evidence
                                                └──→ Audit
```

The Web UI and FastAPI application coordinate PostgreSQL-owned business state,
Redis-carried background work, Qdrant semantic candidates, governed artifact
storage, processing plugins, an isolated Office renderer, and the Notes
collaboration carrier. PostgreSQL remains authoritative for access, audit,
conversations, routing, processing, and durable Notes content.

See [Architecture and trust boundaries](docs/architecture.md) for exact
authority, access, evidence, failure, and lifecycle behavior.

## Start Atlas locally

You need Git, Docker Engine with Docker Compose v2, enough memory and disk for
the complete stack and pinned embedding-model cache, and a fresh disposable
evaluation environment.

```sh
git clone https://github.com/redstone39/Atlas.git
cd Atlas
docker compose up --build -d
```

No `.env` file is required for a fresh local evaluation. The root `compose.yml`
starts the complete local stack and creates persistent deployment secrets when
explicit overrides are absent.

Open <http://127.0.0.1:5184/login>. An empty deployment redirects to the
resumable guided setup:

**Administrator → Model → Project → Document → Review**

Model, Project, and Document may be skipped, but a first governed answer needs a
tested default model route, authorized Project knowledge, and searchable
evidence. Provider credentials are required for model answers and are not
included.

For initializer observation, health and readiness checks, restart, recovery,
replacement, and destructive reset behavior, read
[Local Docker Compose deployment](docs/deployment/local.md). The documented
`down -v` path permanently deletes the Compose project's local application
data. [Configuration](docs/configuration.md) owns the exact first-admin, secret,
Provider, MCP, and runtime input rules.

## What you can evaluate

### Human knowledge work

- Upload authorized documents into a Project or Team and wait until they are
  **Searchable**.
- Organize scope-bound collaborative Notes.
- Ask a question through a General or In-depth conversation.
- Inspect the answer, execution state, and available protected evidence under
  current access.

Evidence review helps inspect what Atlas used; it is not a truth or formal
citation guarantee. Model answers are not deterministic.

### Agent knowledge access

A System Admin can create an Agent identity, issue its opaque bearer token, and
grant it Project access. An MCP 2025-06-18 client connects to exact `/mcp` and
can:

1. discover currently authorized Project scopes;
2. submit one bounded, single-round research request;
3. poll until the immutable research packet is available;
4. read selected protected evidence.

The research packet is the primary result. An optional governed answer remains
bound to that packet. Fresh requests and protected evidence reads recheck
current authorization.

No sample knowledge, private test documents, Provider keys, or expected model
answers are included in the public snapshot.

## Current public boundary

- Atlas is self-hosted rather than a hosted or turnkey service.
- The supported general-developer path is a fresh, loopback-only technical
  evaluation. Keep the default Compose ports bound to loopback.
- Public Internet deployment, in-place application-data migration, high
  availability, automatic failover, and shared-state multi-deployment are not
  supported.
- Managed TLS, backup, monitoring, capacity management, abuse protection, and
  an operations SLA are not provided.
- The default MCP transport accepts localhost Host and Origin values only.
  Configuring an operator-managed proxy or public URL does not make this
  snapshot Internet Ready.
- LDAP and Active Directory ship unconfigured. Portainer with SMB guides and
  audits exist, but real-environment operation is not verified here.

Replacing a snapshot requires a fresh application data set. Identities,
conversations, routing configuration, processing state, and other application
data are not migrated.

These boundaries are open to discussion. If Atlas is missing something
important for your use case, share the scenario, expected outcome, and relevant
environment details through
[Issues](https://github.com/redstone39/Atlas/issues) or
[Discussions](https://github.com/redstone39/Atlas/discussions).

## Administration

![Atlas System Admin settings in a fresh local evaluation deployment](docs/assets/atlas-admin-settings.png)

*System Admin exposes identity, Projects and Teams, the document library,
Models, Skill slots, conversation-learning admission, processing plugins,
agents, conversation and operation audits, Agent Research Audit, system status,
language, theme, Notes settings, and guided-setup re-entry. The pictured account
is local evaluation data.*

## Documentation

| Your question | Start here |
|---|---|
| How does Atlas work and who owns each decision? | [Architecture and trust boundaries](docs/architecture.md) |
| How do first-admin setup, secrets, Providers, directory identity, MCP, and runtime limits work? | [Configuration](docs/configuration.md) |
| How do I operate or reset a fresh local deployment? | [Local deployment](docs/deployment/local.md) |
| How do I verify the public source tree? | [Verification](docs/verification.md) |
| How can I report feedback or discuss Atlas? | [Community and feedback](CONTRIBUTING.md) |

## Engineering methodology

Atlas is also the reference workload for a harness-engineered development
methodology. Product intent, requirements, architecture, risk, and final
acceptance remain human-owned; bounded coding agents work against repository
contracts, tests, audits, and reproducible operator journeys. Read
[Development methodology](docs/development-methodology.md) for the exact public
boundary and evidence exposed by this snapshot.

## Community and license

Atlas is feedback-open and discussion-open. External pull requests and
unsolicited patches are not currently accepted.

Use [GitHub Issues](https://github.com/redstone39/Atlas/issues) for specific,
reproducible defects and reports. Use
[GitHub Discussions](https://github.com/redstone39/Atlas/discussions) for
questions, experiences, architecture discussion, and broader ideas. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the complete participation boundary.

Do not publish vulnerabilities, credentials, private documents, exploit
details, or unredacted logs; follow the private-reporting direction in
[SECURITY.md](SECURITY.md). No support SLA or implementation timeline is
offered.

Atlas source in this repository is licensed under Apache License 2.0. See
[LICENSE](LICENSE), [NOTICE](NOTICE), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
