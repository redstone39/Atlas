# Offline Portainer bundle

`infra/scripts/build_portainer_smb_offline_bundle` builds a platform-specific,
image-only delivery directory for `linux/amd64` or `linux/arm64`.

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

- a seven-image archive;
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

The current data lifecycle is `resettable_development`. Different software
bundle versions do not support in-place application-data migration or software
rollback. Same-version SMB coordinate changes use the generation procedure in
[Portainer with SMB](portainer-smb.md).

Package verification proves the delivered files, image references, platform,
and locked offline assets. It does not prove a real Portainer import, SMB
permissions, network behavior, browser interaction, or production readiness.
