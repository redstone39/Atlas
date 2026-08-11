# Offline Portainer bundle

`infra/scripts/build_portainer_smb_offline_bundle` builds a platform-specific,
eight-image delivery directory for `linux/amd64` or `linux/arm64`. The eighth
reference is the separate content-digest-tagged embedding-model image.

The packaging workstation needs Docker, network access for base images and
locked build assets, and enough disk space. The target Portainer environment
does not need Git, a registry, source code, a host shell, or Internet access.

Example:

```sh
infra/scripts/build_portainer_smb_offline_bundle \
  --version 2026.07.31-1 \
  --platform linux/amd64 \
  --output-dir /absolute/empty/output-directory
```

The output contains:

- one archive containing all eight unique image references;
- flattened `docker-compose.yml` with `pull_policy: never`;
- `bundle-manifest.json` and `IMAGE-LOCK.json`;
- `SHA256SUMS`;
- operator README and SMB runbook;
- third-party notices.

The output directory must be empty. Build `linux/amd64` and `linux/arm64`
separately; do not retag an archive for another architecture.

Before import, verify every `SHA256SUMS` entry and confirm the manifest platform
matches the Docker host. If an image is missing, re-import the same verified
archive rather than enabling registry pulls or changing tags.

After deploying the uploaded Compose, confirm `embedding-model-init` exits `0`
before `api`, `celery-processing`, or `celery-indexing` starts. Its final safe
status must report `mode=offline_verify` and content digest
`e052ba4b733767ddea9fd3e6640ff41a0a83599baea0b0eadd97189d92f2d396`.
Missing or modified model bytes are a startup failure; do not enable a runtime
download or registry fallback.

For a failed model initialization, verify the archive checksums and the model
image ID from `bundle-manifest.json`, then re-import the same archive. If the
image is correct but the initialized cache is invalid, stop the stack, remove
only the `atlas-production-fastembed` volume, and redeploy the same bundle.
Do not remove PostgreSQL, Redis, Qdrant, artifact, or SMB data volumes.

The generated Compose accepts only uppercase `HTTP_PROXY`, `HTTPS_PROXY`, and
`NO_PROXY` stack variables. They are passed only to `api` and
`celery-processing`. Atlas prepends its fixed internal-service bypass list to
`NO_PROXY`; an operator value extends rather than replaces that list. Lowercase
proxy inputs are ignored.

The current data lifecycle is `resettable_development`. Different software
bundle versions do not support in-place application-data migration or software
rollback. Same-version SMB coordinate changes use the generation procedure in
[Portainer with SMB](portainer-smb.md).

Package verification proves the delivered files, image references, platform,
and locked offline assets. It does not prove a real Portainer import, SMB
permissions, network behavior, browser interaction, or production readiness.
