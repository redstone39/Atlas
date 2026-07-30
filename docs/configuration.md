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
- Azure transport only: `ATLAS_AZURE_PROXY_URL`

The Compose files provide local defaults for internal service addresses.
`/api/v1/ops/readiness` reports safe blockers without returning credentials or
raw Provider payloads.

## Local files

Copy `infra/.env.example` to `infra/.env`. The real `.env` is ignored by Git.
Never commit `.env`, private keys, Provider keys, SMB credentials, database
dumps, uploaded documents, or generated offline bundles.
