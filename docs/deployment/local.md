# Local Docker Compose deployment

This path is for a fresh, loopback-only technical evaluation.

## Start

```sh
git clone https://github.com/redstone39/Atlas.git
cd Atlas
docker compose up --build -d
```

The root `compose.yml` loads the complete local stack and is the supported entry
point for general developer evaluation.

A fresh local evaluation needs no `.env` file. Before API and Notes consumers
start, `deployment-secret-init` generates credential-encryption and two distinct
Notes secrets into dedicated named volumes. Existing secret files are reused on
later starts. The stack also starts PostgreSQL, Redis, Qdrant, artifact
initialization, the pinned embedding-model cache, API, the Notes collaboration
carrier, plugin runner, Office renderer, four Celery workers, Celery beat, and
Web. Consumers remain gated when a required initializer fails.

Use `infra/.env.example` only when deliberately supplying overrides; copy it to
`.env` in the repository root before editing. Provide
`ATLAS_CREDENTIAL_MASTER_KEY` and `ATLAS_CREDENTIAL_MASTER_KEY_ID` together. If
you override the two Notes secrets, both must be non-empty and distinct. Never
commit the resulting `.env` file.

## Observe

```sh
docker compose ps
docker compose logs deployment-secret-init
docker compose logs embedding-model-init
docker compose logs artifact-storage-init
curl -fsS http://127.0.0.1:8012/api/v1/ops/health
curl -fsS http://127.0.0.1:8012/api/v1/ops/readiness
```

Open <http://127.0.0.1:5184/login>. When Identity has no users, Atlas redirects
to `/setup`. Create the first System Admin with a display name, unique email,
and password of at least 12 characters. The claim is serialized so only one
concurrent request succeeds, and it never reopens after any user exists.

The same setup journey then guides the signed-in administrator through
**Administrator → Model → Project → Document → Review**. Model, Project, and
Document can be skipped and resumed. The Review step reports what is complete
and links back to incomplete work; readiness may remain degraded until a tested
default model route, an active Project grant, and searchable evidence exist.

The Notes carrier is published only on `127.0.0.1:8015` by default, and API
readiness includes its authenticated settings probe. A running container alone
is not proof that Atlas is ready; use initializer results, health, readiness,
and the setup review.

## Recover a failed first start or setup

If `deployment-secret-init` fails because an explicit credential-key pair is
partial or invalid, or explicit Notes secrets are equal, correct or remove
those overrides and rerun the idempotent startup command:

```sh
docker compose up -d
```

Recheck all three initializer logs, service state, health, and readiness using
the commands in **Observe**. Existing generated secret files take precedence as
the deployment's retained identity; do not delete or replace their volumes to
rotate secrets. Losing the credential secret volume makes previously encrypted
Provider and directory credentials unreadable unless the exact active key is
restored through a retained override or backup.

If first-admin submission did not succeed and Identity is still empty, reload
`/setup` and retry. If another request completed first, the claim is closed;
sign in with that existing account. Do not repair Identity records with manual
SQL.

If the environment is still disposable and you need a completely fresh first
initialization, use the destructive **Reset** procedure below, correct the
configuration, and start again. Do not use reset when application data must be
preserved: `down -v` permanently deletes the Compose project's local
application data and generated deployment secrets.

## Restart

```sh
docker compose up -d
```

The deployment-secret initializer reuses the retained secret files. Identity
state keeps the first-admin claim closed, while the setup journey remains
available to the System Admin for reviewing or completing optional steps.

## Replace an earlier snapshot

Snapshot versions do not support in-place application-data migration. To run a
new snapshot, stop the earlier stack and remove its disposable volumes before
building and starting the new version:

```sh
docker compose down -v
docker compose up --build -d
```

`down -v` permanently deletes PostgreSQL, Redis, Qdrant, and other named-volume
state for this Compose project. Preserve any operator-managed source material
that must be uploaded again. Do not point the new snapshot at an earlier Atlas
database or at artifact storage still owned by another deployment. Identities,
active or archived conversations, audit records, routing configuration, and
runtime history are not migrated between snapshots.

## Reset

```sh
docker compose down -v
```

This destroys the Compose project's local application data. Use it only for the
documented `resettable_development` lifecycle. Uploaded external data and
operator-managed mounts must be handled separately.

## Exposure boundary

The base ports bind to `127.0.0.1`. This repository does not configure TLS,
reverse-proxy authentication, firewall rules, backups, monitoring, or abuse
controls. Do not change the bind addresses for public exposure without a
separate security and operations design.

For a browser on another host, `ATLAS_NOTES_COLLABORATION_PUBLIC_URL` must name
the separately secured browser-reachable `ws://` or `wss://` endpoint. Changing
the host bind or adding TLS/reverse proxy exposure is outside this local
loopback-only path.

The MCP Agent research carrier is mounted on the existing API service at exact
`/mcp`; the Compose stack adds no MCP service or port. With
`ATLAS_MCP_PUBLIC_URL` empty, only localhost/`127.0.0.1` Host and Origin values
are accepted. Keep this default for the supported local path.

If an operator separately places a secured reverse proxy in front of MCP, set
`ATLAS_MCP_PUBLIC_URL` to its HTTP(S) origin or exact `/mcp` URL. The proxy must
preserve the original `Host` and `Origin`, forward `Authorization`,
`MCP-Session-Id`, and `MCP-Protocol-Version`, and pass Streamable HTTP responses
without buffering or rewriting. The operator owns TLS, authentication, rate and
size limits, timeouts, logging controls, firewalling, and abuse protection.
Invalid public-URL syntax fails API startup. This input does not make the
snapshot Internet Ready or authorize changing the loopback port bindings.
