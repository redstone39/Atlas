# Atlas Processing SDK

`atlas-processing-sdk` is the standalone typed contract and CLI
for Atlas processing plugins. Plugins receive opaque artifact references and emit
pre-KPEL drafts only; Atlas Core retains authorization, validation, promotion,
storage, audit, citation, and canonical evidence ownership.

```bash
atlas-plugin init my-plugin --kind region-processor
cd my-plugin
atlas-plugin test .
atlas-plugin build . --output dist/my-plugin.atlas-plugin
atlas-plugin verify dist/my-plugin.atlas-plugin
```

Production packages are signed with an Ed25519 team key:

```bash
atlas-plugin build . --output dist/my-plugin.atlas-plugin \
  --signing-key team-private-key.pem --key-id team-key-2026
```

The package contains exactly one generated `plugin.whl`, canonical manifest and
checksums, schemas, smoke fixture/expected output, `requirements.lock`, SPDX
SBOM, and signature envelope. Local unsigned builds require explicit
`--allow-unsigned` when verified and cannot be activated by Production.

Admin commands require `--base-url` and `--token` (or `ATLAS_BASE_URL` and
`ATLAS_TOKEN`). Mutation commands automatically send an idempotency key unless
one is explicitly supplied.
