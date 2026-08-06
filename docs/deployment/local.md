# Local Docker Compose deployment

This path is for a fresh, loopback-only technical evaluation.

## Start

```sh
cp infra/.env.example infra/.env
# Edit infra/.env and set the two bootstrap administrator values.
cd infra
docker compose -f docker-compose.p1.yml up --build -d
```

The stack starts PostgreSQL, Redis, Qdrant, artifact initialization, API,
plugin runner, Office renderer, four Celery workers, Celery beat, and Web.
Services that depend on initialization remain gated when it fails.

## Observe

```sh
docker compose -f docker-compose.p1.yml ps
docker compose -f docker-compose.p1.yml logs artifact-storage-init
curl -fsS http://127.0.0.1:8012/api/v1/ops/health
curl -fsS http://127.0.0.1:8012/api/v1/ops/readiness
```

Use <http://127.0.0.1:5184/login>. A running container alone is not proof that
Atlas is ready; use the initializer result, health, readiness, and a real login.

## Restart

After the first administrator exists, remove the bootstrap values from
`infra/.env` if desired:

```sh
cd infra
docker compose -f docker-compose.p1.yml up -d
```

The initializer observes non-empty Identity state and does not require or alter
the original credentials.

## Replace an earlier snapshot

Snapshot versions do not support in-place application-data migration. To run a
new snapshot, stop the earlier stack and remove its disposable volumes before
building and starting the new version:

```sh
cd infra
docker compose -f docker-compose.p1.yml down -v
docker compose -f docker-compose.p1.yml up --build -d
```

`down -v` permanently deletes PostgreSQL, Redis, Qdrant, and other named-volume
state for this Compose project. Preserve any operator-managed source material
that must be uploaded again. Do not point the new snapshot at an earlier Atlas
database or at artifact storage still owned by another deployment. Identities,
active or archived conversations, audit records, routing configuration, and
runtime history are not migrated between snapshots.

## Reset

```sh
cd infra
docker compose -f docker-compose.p1.yml down -v
```

This destroys the Compose project's local application data. Use it only for the
documented `resettable_development` lifecycle. Uploaded external data and
operator-managed mounts must be handled separately.

## Exposure boundary

The base ports bind to `127.0.0.1`. This repository does not configure TLS,
reverse-proxy authentication, firewall rules, backups, monitoring, or abuse
controls. Do not change the bind addresses for public exposure without a
separate security and operations design.
