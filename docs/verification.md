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
tree. The audit verifies the repository's encoded publication boundary; it does
not inspect external services or certify a deployment.

## Public first-run smoke

With the public Compose stack running on its default loopback ports, exercise
the empty-deployment setup journey:

```sh
infra/scripts/smoke_public_first_run
docker compose -f infra/docker-compose.p1.yml restart
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

Success prints `PUBLIC_CONVERSATION_EVOLUTION_ACCEPTED`.

PostgreSQL integration tests also require a dedicated disposable test database
and refuse non-test database names. See `api/scripts/check-postgres`.

## Interpreting results

A passing command is evidence only for the behavior that command exercises.
Unit tests are not a substitute for the user journey, and a healthy local stack
is not proof of production readiness. Keep controlled repository checks, local
runtime operation, browser verification, live Provider behavior, and target
deployment acceptance as separate claims.
