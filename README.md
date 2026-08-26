# Atlas

Atlas is a self-hosted knowledge platform for people and agents.

People use Atlas to organize governed documents, collaborate in Notes, and ask
questions with inspectable evidence. Agents use Atlas through MCP to research
governed Project knowledge and read the evidence behind their results.

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

*The Atlas Web workspace after guided setup—the human entry point for governed
Projects, Teams, documents, Notes, conversations, and administration. Provider
credentials and sample knowledge are not included in this public snapshot.*

## Why Atlas

- **One governed knowledge platform:** People work through the Web UI while
  Agents research governed Project knowledge through MCP. Documents, Notes,
  identity, model routing, administration, and Agent access run in one
  self-hosted deployment.
- **Authorization stays current:** Atlas evaluates access when knowledge is used
  and rechecks protected evidence reads before returning content.
- **Results stay inspectable:** Human answers and Agent research expose evidence
  and auditable execution state instead of acting like opaque model output.

## For people and agents

| | People | Agents |
|---|---|---|
| **Entry point** | Web workspace | MCP |
| **Knowledge access** | Project and Team knowledge | Project knowledge |
| **Primary work** | Manage documents and Notes; ask through General or In-depth | Discover scopes, run one-round research, retrieve results, and inspect evidence |
| **Result** | Answer, execution state, and inspectable evidence | Research packet, optional grounded answer, and inspectable evidence |

Agent access is intentionally narrower than the human workspace in this public
snapshot. Agents do not upload documents or edit Notes through MCP.

## How Atlas works

```text
People ──→ Web workspace ──→ Project / Team knowledge ──→ Answers + evidence
Agents ──→ MCP research ───→ Project knowledge ──→ Packets + evidence
```

Human conversations can use Project or Team knowledge, while MCP Agents are
limited to Project scopes granted to their identity. Both paths expose
inspectable evidence and auditable results through the same Atlas deployment.

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
tested default model route, accessible Project knowledge, and searchable
evidence.

For health checks, recovery, replacement, and lifecycle details, see
[Local Docker Compose deployment](docs/deployment/local.md). Its documented
`down -v` reset permanently deletes local Atlas application data. For secrets,
Providers, MCP, and runtime inputs, see
[Configuration](docs/configuration.md).

## What you can evaluate

### Human knowledge work

1. Add documents to a Project or Team and wait until they are **Searchable**.
2. Create or collaborate in scope-bound Notes.
3. Ask a question through a General or In-depth conversation.
4. Inspect the answer, execution state, and available evidence.

Evidence review helps inspect what Atlas used; it is not a truth or formal
citation guarantee. Model answers are not deterministic.

### Agent knowledge access

A System Admin can create an Agent identity, issue its opaque bearer token, and
grant it Project access.

1. Discover available Project scopes.
2. Submit one bounded, single-round research request.
3. Retrieve the research packet.
4. Inspect selected evidence.

An MCP 2025-06-18 client connects to exact `/mcp`. The research packet is the
primary result, and an optional governed answer remains bound to it. Fresh
requests and protected evidence reads re-evaluate access.

## Current public boundary

### Supported for evaluation

- Fresh local Docker Compose deployment with loopback Web and MCP access.
- Governed documents, collaborative Notes, scoped conversations, and
  inspectable evidence.
- Local Provider and model configuration.
- One-round MCP Agent research over Project knowledge.

### Current limits

- No direct public Internet deployment.
- No in-place application-data migration; replacing a snapshot requires a fresh
  application data set.
- No high availability, automatic failover, or shared-state multi-deployment.
- No managed TLS, backup, monitoring, capacity management, abuse protection, or
  operations SLA.

Keep the default Compose ports bound to loopback. The default MCP transport
accepts localhost Host and Origin values only; configuring an operator-managed
proxy or public URL does not make this snapshot Internet Ready.

LDAP and Active Directory ship unconfigured. Portainer with SMB paths are
documented and audited, but real-environment operation is not verified in this
public snapshot.

These boundaries are open to discussion. If Atlas is missing something
important for your use case, share the scenario, expected outcome, and relevant
environment details through
[Issues](https://github.com/redstone39/Atlas/issues) or
[Discussions](https://github.com/redstone39/Atlas/discussions).

## Administration

![Atlas System Admin settings in a fresh local evaluation deployment](docs/assets/atlas-admin-settings.png)

*System Admin manages identity, Projects and Teams, documents, models,
processing, agents, audits, system status, language, theme, Notes settings, and
guided setup. The pictured account is local evaluation data.*

## Documentation

| Your question | Start here |
|---|---|
| How does Atlas work and who owns each decision? | [Architecture and trust boundaries](docs/architecture.md) |
| How do first-admin setup, secrets, Providers, directory identity, MCP, and runtime limits work? | [Configuration](docs/configuration.md) |
| How do I operate or reset a fresh local deployment? | [Local deployment](docs/deployment/local.md) |
| How do I verify the public source tree? | [Verification](docs/verification.md) |
| How can I report feedback or discuss Atlas? | [Community and feedback](CONTRIBUTING.md) |

## Engineering methodology

Atlas is also a reference workload for a harness-engineered development
methodology: humans retain ownership of product intent, architecture, risk, and
acceptance, while bounded coding agents work against explicit repository
contracts and verification. See
[Development methodology](docs/development-methodology.md) for details.

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
