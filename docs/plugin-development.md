# Processing plugin development

Atlas processing plugins are signed `.atlas-plugin` packages. Atlas owns
transport, isolation, routing, authorization, promotion, normalization,
persistence, citation, and audit.

## Developer path

Python 3.12 and `uv` are sufficient for scaffold, fixture execution,
conformance, and package creation:

```sh
uvx --refresh-package atlas-processing-sdk --from ./plugin-sdk \
  atlas-plugin init /tmp/example-parser --kind region-processor
cd /tmp/example-parser
uv run atlas-plugin dev --fixture tests/fixtures/sample.json
uv run atlas-plugin test
uv run atlas-plugin build \
  --signing-key /secure/team-ed25519-private.pem \
  --key-id team-key-2026
uv run atlas-plugin verify dist/*.atlas-plugin
```

The private signing key must never enter the repository, plugin directory, or
package. Unsigned packages are accepted only when local development explicitly
enables them.

Plugins may emit typed parsing candidates and optional preview geometry. They
must not emit access decisions, canonical records, evidence/citation/audit
identities, index operations, database mutations, credentials, raw host paths,
or raw filenames.

## Runtime profiles

- `atlas-python-v1`: `pypdf==6.0.0`
- `atlas-docling-cpu-v1`: `docling==2.111.0`, `pypdf==6.0.0`

`requirements.lock` accepts exact `name==version` entries that already exist in
the selected profile. Plugin installation does not resolve dependencies over
the network.

Tracked examples are under:

- `plugin-sdk/examples/atlas-pypdf`
- `plugin-sdk/examples/atlas-docling-pdf`

## Administration

Use `/admin/plugins` or the SDK CLI:

```sh
uv tool install ./plugin-sdk
printf '%s\n' "$ATLAS_ADMIN_PASSWORD" | atlas-plugin admin \
  --base-url https://atlas.example login \
  --email admin@example.com --password-stdin
atlas-plugin admin package upload dist/plugin.atlas-plugin
```

The CLI stores the base URL and revocable session token under
`~/.config/atlas/plugin.json` with mode `0600`. Passwords are read from stdin
and are not stored.
