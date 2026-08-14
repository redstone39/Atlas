# Portainer with SMB

This optional path targets one rootful Linux Docker Standalone environment
managed by Portainer 2.39 LTS. Real Portainer and SMB operation is not verified
by this repository's local acceptance.

Atlas creates a Docker local CIFS volume that connects to an existing SMB share.
It does not create an SMB server or install host CIFS support. Windows without
Docker, Swarm, rootless Docker, and multiple Atlas deployments sharing
authoritative resources are unsupported.

## Composition

For source-based evaluation, use:

```sh
docker compose -f infra/docker-compose.portainer-smb.yml config
```

The file combines `docker-compose.p1.yml` with the SMB override. Remote
Portainer environments should use the image-only offline bundle instead of a
Git build context.

## Required stack variables

- `ATLAS_BOOTSTRAP_ADMIN_EMAIL` and `ATLAS_BOOTSTRAP_ADMIN_PASSWORD` for an
  empty Identity database.
- `ATLAS_SMB_HOST`, `ATLAS_SMB_SHARE`, `ATLAS_SMB_SUBDIR`
- `ATLAS_SMB_USERNAME`, `ATLAS_SMB_PASSWORD`
- `ATLAS_SMB_GENERATION`, initially `1`
- `ATLAS_ARTIFACT_SWITCH_MODE=operator_accepted_unverified`
- `ATLAS_ARTIFACT_SWITCH_ACK=I_ACCEPT_UNVERIFIED_BLOB_MAPPING_AND_CONTENT`
- `ATLAS_CREDENTIAL_MASTER_KEY` and `ATLAS_CREDENTIAL_MASTER_KEY_ID` before
  storing Provider or LDAP/Active Directory bind and custom-CA credentials
- `ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET` and
  `ATLAS_NOTES_COLLABORATION_TICKET_SECRET`, generated independently and kept
  distinct
- `ATLAS_NOTES_COLLABORATION_PUBLIC_URL`, set to the separately secured
  browser-reachable `ws://` or `wss://` endpoint for this remote deployment

Optional SMB variables are `ATLAS_SMB_DOMAIN` and
`ATLAS_SMB_VERSION` (`3.1.1` by default; `3.0` is also supported).

`ATLAS_SMB_SUBDIR` must be a relative path without empty segments, `.`, `..`,
backslashes, commas, or control characters. Use a dedicated SMB account limited
to the selected share and directory.

## Provider runtime proxy

Only the `api` and `celery-processing` services receive process-wide Provider
proxy settings:

| Variable | Default | Behavior |
|---|---|---|
| `HTTP_PROXY` | empty | Standard process HTTP proxy |
| `HTTPS_PROXY` | empty | Standard process HTTPS proxy |
| `NO_PROXY` | empty | Appended after Atlas's fixed internal bypass prefix |

Only uppercase stack variables are accepted. Lowercase `http_proxy`,
`https_proxy`, and `no_proxy` inputs are ignored. Atlas always prepends
`::1,127.0.0.1,localhost,postgres,redis,qdrant,plugin-runner,office-renderer,api,web`
to `NO_PROXY`; operators may append organization addresses but cannot replace
the internal bypass list. Other services do not receive these variables.

Changing proxy variables does not require a new SMB generation. Avoid embedding
credentials in proxy URLs unless exposure to Portainer and Docker
administrators is acceptable.

## Initialization

Confirm `embedding-model-init` exits `0` before the API and processing/indexing
workers start. It verifies the separate embedding-model image into the shared
`atlas-production-fastembed` volume without a network fallback. A missing or
digest-invalid model cache intentionally keeps those consumers stopped; use
the recovery procedure in [Offline Portainer bundle](portainer-smb-offline.md).

Confirm `artifact-storage-init` exits `0` and its last JSON record includes:

- `"status":"succeeded"`
- the requested generation
- `"verification_mode":"operator_accepted_unverified"`
- `"evidence_claim":"OPERATOR_ACCEPTED_UNVERIFIED_TARGET"`

The initializer probes the mounted filesystem and changes the active target and
fence. It does not enumerate and hash every existing blob. Missing referenced
files or incorrect sizes fail later reads closed; ordinary reads do not detect
same-size content substitution.

## SMB changes and recovery

Docker local-volume driver options are immutable. When SMB coordinates,
credentials, or mount options change:

1. Stop the stack.
2. Update the variables.
3. Increase `ATLAS_SMB_GENERATION`.
4. Update the stack.
5. Remove a detached old Docker volume only after the new initializer succeeds.

Rollback to an older SMB location still requires a generation greater than
every previously used value.

Portainer stack variables and Docker volume driver options are visible to
Portainer/Docker administrators. SMB credentials are not Docker Secrets on this
Standalone path. Never place them in logs or support reports.
