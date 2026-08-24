# Verification

These checks provide reproducible evidence about the checked-out Atlas source
tree. Run only the checks relevant to your change, then expand to the complete
set before making a broad public-snapshot claim.

Passing them does not verify browser presentation, live Provider behavior, real
LDAP/Active Directory interoperability, a real Portainer/SMB environment,
production capacity, Internet exposure, or answer truth. Those require separate
evidence in the target environment.

## Core checks

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

## Public snapshot boundary

The publication-boundary audit checks either the staged index or the committed
public tree:

```sh
infra/scripts/audit_public_snapshot_boundary
infra/scripts/audit_public_snapshot_boundary --committed
```

Use the first form while preparing a commit and the second against a committed
tree. Direct conversation and Provider fixtures must carry an explicit accepted
synthetic marker such as `public-synthetic`. `--committed` evaluates the entire
committed tree, not only the paths or syntax involved in the current command.
The audit verifies the repository's encoded publication boundary; it does not
inspect external services or certify a deployment.

## Public first-run smoke

With the public Compose stack running on its default loopback ports, exercise
the empty-deployment setup journey:

```sh
export ATLAS_PUBLIC_SMOKE_PROJECT=atlas_public_first_run
docker compose -p "$ATLAS_PUBLIC_SMOKE_PROJECT" up --build -d
infra/scripts/smoke_public_first_run
docker compose -p "$ATLAS_PUBLIC_SMOKE_PROJECT" restart
infra/scripts/smoke_public_first_run --verify-restart
```

The fresh pass claims the first System Admin concurrently, configures owner-
allocated resources, uploads one document, and records state for the restart
pass. The restart pass confirms that the first-admin gate stays closed and the
created state remains available. Use only a disposable Compose project and
remove its volumes after the check.

## Conversation-evolution smoke

The conversation-evolution smoke requires `ATLAS_TEST_POSTGRES_URL` to name a
dedicated disposable PostgreSQL database whose name starts with
`atlas_baseline_test_`:

```sh
ATLAS_TEST_POSTGRES_URL='postgresql+psycopg://atlas:atlas@127.0.0.1:5432/atlas_baseline_test_public_upgrade' \
  PYTHONPATH=api/src uv run --project api \
  python api/scripts/smoke_public_conversation_evolution.py
```

URL parsing, PostgreSQL-driver validation, and database-name allowlisting run
before runtime construction, schema bootstrap, or cleanup. An accepted database
is still mutated: the script bootstraps the schema and deletes its own
conversation-evolution rows. Use a dedicated, disposable, unshared database.
Success prints `PUBLIC_CONVERSATION_EVOLUTION_ACCEPTED`.

This targeted cleanup differs from `api/scripts/check-postgres`, which resets
the database's public schema. PostgreSQL integration tests enforce the same
dedicated-test-database naming boundary.

## P1 agent-access smoke

Prerequisites: Docker with Compose, `curl`, Python 3, and OpenSSL. Assign a unique
disposable Compose project; the script starts the P1 stack, claims a generated
first administrator, exercises agent-token grant and revocation, and removes
only that named project's volumes on exit:

```sh
ATLAS_PRODUCTION_COMPOSE_PROJECT="atlas-p1-owner-smoke-$(date +%s)" \
  infra/scripts/smoke_p1_agent_access
```

Exit status `0` is the success evidence. This is an API/HTTP smoke and provides
no browser evidence.

## P2 RBAC smoke

Prerequisites: Docker with Compose, `curl`, and Python 3. The script owns a
uniquely named disposable P1 stack, creates its first administrator, checks
direct and inherited RBAC decisions across an API restart, and removes that
project's volumes on exit:

```sh
ATLAS_PRODUCTION_COMPOSE_PROJECT="atlas-p2-owner-smoke-$(date +%s)" \
  infra/scripts/smoke_p2_rbac_access
```

Exit status `0` after the persisted-access assertions is the success evidence.
The Web-origin checks are HTTP checks, not browser evidence.

## Collaborative Notes smoke

Prerequisites: Docker with Compose, `curl`, Python 3, Node.js, and the
collaboration-server package dependencies. The script allocates free loopback
ports, claims a generated first administrator, exercises Project and inherited
Team collaboration behavior, and removes its named disposable volumes and
temporary provider files on exit:

```sh
ATLAS_PRODUCTION_COMPOSE_PROJECT="atlas-notes-owner-smoke-$(date +%s)" \
  infra/scripts/smoke_collaborative_notes
```

Success prints `Collaborative Notes fresh-stack smoke passed: ...`. This is an
API/WebSocket smoke and provides no browser evidence.

## Multiformat acceptance smoke

Prerequisites: Docker with Compose, Python 3, `uv`, the plugin-runner and API
project dependencies, and LibreOffice's `soffice` executable. The Compose
overlay supplies a deterministic Provider; no live Provider credential is
used.

```sh
fixtures="$(mktemp -d)"
uv run --project plugin-runner python \
  infra/scripts/generate_multiformat_fixtures.py --output "$fixtures"

project="atlas-multiformat-owner-smoke-$(date +%s)"
docker compose -p "$project" \
  -f infra/docker-compose.p1.yml \
  -f infra/docker-compose.multiformat-acceptance.yml \
  up --build -d --wait

export ATLAS_MULTIFORMAT_ADMIN_EMAIL="multiformat-admin@example.test"
export ATLAS_MULTIFORMAT_ADMIN_PASSWORD="MultiformatSmoke-Only-01!"
uv run --project api python \
  infra/scripts/smoke_multiformat_acceptance.py --fixtures "$fixtures"
uv run --project api python \
  infra/scripts/smoke_multiformat_acceptance.py \
  --fixtures "$fixtures" --configuration-ready
uv run --project api python \
  infra/scripts/smoke_multiformat_acceptance.py \
  --fixtures "$fixtures" --journey-only --run-suffix "$(date +%s)"

docker compose -p "$project" \
  -f infra/docker-compose.p1.yml \
  -f infra/docker-compose.multiformat-acceptance.yml \
  down -v --remove-orphans
```

Default mode requires a fresh stack and claims/configures the first
administrator. `--configuration-ready` and `--journey-only` require an existing
configured stack, log in, and resolve the uniquely named acceptance resources.
Each successful invocation emits JSON with `"status": "passed"`. Confirm the
Compose project is disposable before `down -v`; the Python smoke itself does not
own or clean volumes. These modes provide API/HTTP evidence, not browser
evidence.

## Human-operable browser smoke

This proof requires Docker with Compose, installed Web/Playwright dependencies,
a local Chromium-compatible executable, and a live reachable Provider key and
model. Start a uniquely named fresh disposable Compose stack; the wrapper does
not start, stop, or clean the runtime:

```sh
export ATLAS_PUBLIC_SMOKE_PROJECT="atlas-human-operable-$(date +%s)"
docker compose -p "$ATLAS_PUBLIC_SMOKE_PROJECT" up --build -d --wait

export ATLAS_PRODUCTION_SMOKE_PROVIDER_API_KEY="<live-provider-key>"
export ATLAS_HUMAN_SMOKE_ADMIN_EMAIL="human-smoke-admin@example.test"
export ATLAS_HUMAN_SMOKE_ADMIN_PASSWORD="<fresh-password-at-least-12-characters>"
# Optional when the Provider or browser differs from the defaults:
export ATLAS_PRODUCTION_PROVIDER_ENDPOINT="https://api.openai.com/v1"
export ATLAS_PRODUCTION_PROVIDER_MODEL="gpt-4.1-mini"
export CHROME_EXECUTABLE_PATH="/path/to/chromium"

infra/scripts/smoke_human_operable
```

Success prints `browser smoke passed: <screenshot>` and creates that screenshot.
It proves the current setup, document, invite/member, governed query/refusal,
revocation, and denied-access browser journey against the live Provider. After
confirming the project name is disposable, clean only that project:

```sh
docker compose -p "$ATLAS_PUBLIC_SMOKE_PROJECT" down -v --remove-orphans
```

## Interpreting results

A passing command is evidence only for the behavior that command exercises.
Unit tests are not a substitute for the user journey, and a healthy local stack
is not proof of production readiness. Keep controlled repository checks, local
runtime operation, browser verification, live Provider behavior, and target
deployment acceptance as separate claims.
