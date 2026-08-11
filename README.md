# Atlas

Atlas is a self-hosted document knowledge workspace for technical evaluation and
controlled demonstrations. It combines document intake, governed processing,
scoped retrieval and conversations, evidence review, local or LDAP/Active
Directory identity, administration, and auditable access controls in one Docker
Compose deployment.

This repository contains a standalone public snapshot of the Atlas runtime. It
is published for source transparency, technical evaluation, and self-hosted
demonstration under the Apache License, Version 2.0.

This public tree is maintained independently. It is not synchronized with the
private development repository and does not represent a production release,
hosted service, security certification, or support commitment. It does not
contain private project plans, decision logs, test documents, deployment
credentials, or build outputs.

## Status and supported use

Atlas currently uses a `resettable_development` data lifecycle. It is suitable
for fresh evaluation environments whose application data may be rebuilt.

Atlas is **not Release Ready or Internet Ready**. It does not claim:

- production identity recovery or credential rotation;
- in-place upgrade compatibility or application-data migration;
- high availability, automatic failover, or multiple deployments sharing state;
- managed TLS, backup, monitoring, capacity management, or abuse protection;
- verified operation on a real Portainer/SMB environment.

Keep the default Compose ports bound to loopback. Do not expose this snapshot to
the public Internet without a separate security and operations review.

Replacing an earlier snapshot requires a fresh application data set. Stop the
earlier stack and remove its Compose volumes before starting this version. Atlas
does not migrate identities, conversations, routing configuration, processing
state, or other application data between snapshot versions. Preserve uploaded
source material outside disposable volumes when it must be loaded again.

## Runtime components

- `web/`: React, TypeScript, and Vite user interface.
- `api/`: FastAPI application, owner use cases, PostgreSQL repositories, and
  migrations.
- `plugin-sdk/` and `plugin-runner/`: controlled processing plugin contracts and
  execution.
- `office-renderer/`: isolated Office document page rendering.
- `infra/`: Docker Compose, operator smoke tests, packaging, and architecture
  audits.

PostgreSQL is authoritative for business, access, audit, conversation, routing,
and processing state. Redis is the task broker, Qdrant is a non-authoritative
semantic candidate index, and local or SMB storage contains governed artifact
bytes.

The separate `embedding-model` image initializes a pinned, digest-verified
offline cache before the API and processing/indexing workers start. Runtime
downloads are intentionally disabled.

See [Architecture](docs/architecture.md) for the trust and failure boundaries.

## Workspace reasoning modes

Workspace turns support `standard` and `deep` reasoning modes. Standard keeps
the normal governed answer flow. Deep runs a bounded plan, research, evaluation,
and revision loop under the selected model route's tool, Provider, token, and
deadline limits.

Workspace shows only durable, allowlisted progress phases. System Admin can
inspect the bounded Atlas-owned plan/evaluation trace for incident review. These
surfaces do not expose Provider reasoning or raw chain-of-thought, and their
process status and scores are not truth, accuracy, or confidence guarantees.

Conversation history treats prior user text as user-provided context and prior
assistant text as pending verification. Historical assistant text can help
resolve dialogue references, but it is not factual evidence for a later answer.
Evidence, page, visual, and navigation handles share one execution-fixed,
deduplicated per-turn limit.

Workspace members can remove an idle conversation from their history with the
Delete action. This archives the conversation; it does not physically delete
the record. Archived conversations remain available to System Admin through the
audited administration surface.

New conversations may freeze an optional set of current Team and Project scopes.
Every fresh or retried turn intersects that selection with the caller's current
access; revoked scopes remain unavailable and an empty intersection never
expands back to all accessible knowledge.

## Local quick start

Requirements:

- Docker Engine with Docker Compose v2;
- enough memory and disk for PostgreSQL, Redis, Qdrant, the API, four workers,
  the plugin runner, Office renderer, and Web UI;
- a fresh evaluation environment.

Create local configuration:

```sh
cp infra/.env.example infra/.env
```

Set at least:

```dotenv
ATLAS_BOOTSTRAP_ADMIN_EMAIL=you@example.com
ATLAS_BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-unique-password
```

The password must contain at least 12 characters. These values are used only
when the Identity database is empty. After the first successful initialization,
the initializer does not rotate or overwrite any existing user. You may remove
the bootstrap values from `infra/.env` before later restarts.

Start the stack:

```sh
cd infra
docker compose -f docker-compose.p1.yml up --build -d
```

Observe the initializer and service state:

```sh
docker compose -f docker-compose.p1.yml logs embedding-model-init artifact-storage-init
docker compose -f docker-compose.p1.yml ps
curl -fsS http://127.0.0.1:8012/api/v1/ops/health
curl -fsS http://127.0.0.1:8012/api/v1/ops/readiness
```

Open <http://127.0.0.1:5184/login> and sign in with the bootstrap credentials.

If initialization fails because bootstrap configuration is missing or invalid,
set the two values and rerun:

```sh
docker compose -f docker-compose.p1.yml up -d
```

Do not repair identity records with manual SQL. For a disposable fresh
evaluation, stop the stack and remove its volumes, correct the configuration,
and start again:

```sh
docker compose -f docker-compose.p1.yml down -v
```

This deletes the Compose project's application data and is appropriate only for
the documented resettable-development lifecycle.

## Provider setup

Before storing Provider credentials or enabling an LDAP/Active Directory
connection, configure `ATLAS_CREDENTIAL_MASTER_KEY` and
`ATLAS_CREDENTIAL_MASTER_KEY_ID`. Provider API keys, directory bind passwords,
and optional custom CA material are entered through System Admin and are not
read from Provider- or directory-specific environment variables.

Provider connections use one of three closed profiles:
`openai_compatible`, `azure_openai`, or `anthropic`. Azure connections require
the resource-root endpoint and an API protocol version; Anthropic uses
`https://api.anthropic.com`. Atlas persists these connection settings and
invokes LiteLLM in-process with the stored credential. Route-less execution
uses only the eligible route explicitly marked as default and never falls back
to another route.

## LDAP and Active Directory

System Admin can configure and test an `ldap` or `active_directory` connection,
search and import directory users, and refresh one imported profile. Atlas
remains authoritative for account activity, system role, grants, ACLs, and
sessions. Local email authentication is checked first; once an imported
directory source is selected, unavailable transport, disabled principals,
invalid credentials, alias conflicts, or a concurrent Atlas deactivation fail
closed without falling through to another source.

The public snapshot ships unconfigured. It contains no directory endpoint,
bind credential, custom CA, or imported identity. Live LDAP/Active Directory
interoperability is not verified by this repository.

See [Configuration](docs/configuration.md) for generation and recovery
requirements.

## Additional operation guides

- [Local deployment](docs/deployment/local.md)
- [Portainer with SMB](docs/deployment/portainer-smb.md)
- [Offline Portainer bundle](docs/deployment/portainer-smb-offline.md)
- [Processing plugin development](docs/plugin-development.md)
- [Security policy](SECURITY.md)
- [Contribution guide](CONTRIBUTING.md)

## Verification

Core checks:

```sh
api/scripts/check
npm --prefix web test
npm --prefix web run build
PYTHONPATH=plugin-sdk/src uv run --project plugin-sdk pytest plugin-sdk/tests
PYTHONPATH=plugin-runner/src uv run --project plugin-runner pytest plugin-runner/tests
PYTHONPATH=office-renderer/src uv run --project office-renderer pytest office-renderer/tests
infra/scripts/audit_architecture_boundaries
infra/scripts/audit_development_baseline
infra/scripts/audit_provider_key_cutover
infra/scripts/audit_third_party_notices
```

PostgreSQL integration tests require a dedicated disposable test database and
refuse non-test database names. See `api/scripts/check-postgres`.

## License

Atlas source in this repository is licensed under Apache License 2.0. See
[LICENSE](LICENSE), [NOTICE](NOTICE), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
