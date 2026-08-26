# Third-party notices

Atlas is licensed separately under Apache License 2.0. This file records direct
runtime selections and their upstream license evidence. It is an engineering
inventory, not legal advice and not a replacement for license texts shipped in
Python wheels, npm packages, models, operating-system packages, or container
images.

Resolved versions are pinned by the committed `uv.lock`,
`web/package-lock.json`, and `collaboration-server/package-lock.json` files.
Distributions must preserve the corresponding upstream license and attribution
files.

## Python runtime dependencies

- MIT family: `alembic` (MIT), `fastapi` (MIT), `jsonschema` (MIT),
  `docling` (MIT), `litellm` (MIT, pinned `1.95.0` as the in-process completion
  carrier), `mcp` 2.1.1 (MIT; official Model Context Protocol Python SDK),
  `openpyxl` (MIT), `python-docx` (MIT), `python-pptx` (MIT), `onnxruntime`
  (MIT), `pyyaml` 6.0.3 (MIT), `sqlalchemy` (MIT), `tiktoken` (MIT), and
  `pillow` (MIT-CMU).
- BSD family: `celery` (BSD-3-Clause), `httpx` (BSD-3-Clause), `pypdf`
  (BSD-3-Clause), `uvicorn` (BSD-3-Clause), and `torchvision` (BSD family).
- Apache family: `fastembed` (Apache-2.0), `python-multipart`
  (Apache-2.0), `qdrant-client` (Apache-2.0), and `rapidocr`
  (Apache-2.0).
- Dual or multi-license selections: `cryptography`
  (Apache-2.0 OR BSD-3-Clause), `torch` (Apache-2.0 plus bundled
  LLVM/BSD/Boost/MIT components), and `pypdfium2` (BSD-3-Clause for the
  wrapper with Apache-2.0/BSD and other notices for PDFium and its bundled
  dependencies).
- Other reciprocal terms: `ldap3` 2.9.1 (LGPLv3), `pikepdf` (MPL-2.0),
  and `psycopg` (LGPL-3.0-only). The locked `pyasn1` dependency used by
  `ldap3` is BSD-2-Clause.

Upstream evidence is available from each package's PyPI project metadata and
the license files installed in its wheel. Distributions must preserve the
`ldap3` and `pyasn1` license files with the other installed package licenses.
The API's `celery[redis]` extra also resolves Redis transport packages whose
license files remain in the locked environment.

## Web runtime dependencies

MIT-licensed direct selections:

`@base-ui/react`, `@hookform/resolvers`, `@radix-ui/react-label`,
`@radix-ui/react-slot`, `@radix-ui/react-tabs`, `@shadcn/react`,
`clsx`, `cmdk`, `date-fns`, `embla-carousel-react`, `i18next`,
`input-otp`, `next`, `next-themes`, `radix-ui`, `react`, `react-day-picker`,
`react-dom`, `react-hook-form`, `react-i18next`, `react-markdown`,
`react-resizable-panels`, `recharts`, `remark-gfm`, `sonner`,
`tailwind-merge`, `vaul`, and `zod`.

The scoped Notes editor additionally selects the following MIT-licensed Web
runtime packages: `@hocuspocus/provider` 4.6.0, `yjs` 13.6.31,
`@tiptap/core`, `@tiptap/extension-collaboration`,
`@tiptap/extension-drag-handle-react`, `@tiptap/extension-file-handler`,
`@tiptap/extension-link`, `@tiptap/extension-table`,
`@tiptap/extension-task-item`, `@tiptap/extension-task-list`,
`@tiptap/extension-underline`, `@tiptap/extension-unique-id`, `@tiptap/pm`,
`@tiptap/react`, and `@tiptap/starter-kit` (all Tiptap packages 3.30.0).

Other direct selections:

- `class-variance-authority`: Apache-2.0.
- `lucide-react`: ISC.
- `pdfjs-dist`: Apache-2.0.

The npm package tarballs and `web/node_modules/<package>/LICENSE*` files are the
authoritative copies for installed versions.

## Notes collaboration runtime dependencies

MIT-licensed direct selections:

- `@hocuspocus/server 4.6.0` and `@hocuspocus/provider 4.6.0`.
- `@hocuspocus/transformer 4.6.0`.
- `yjs 13.6.31` and `y-prosemirror 1.3.7`.
- `jsondiffpatch 0.7.6`.
- The `@tiptap/* 3.30.0` OSS runtime family and `@tiptap/y-tiptap 3.0.8`.

`@dmsnell/diff-match-patch 1.1.0` is Apache-2.0. The package tarballs and
`collaboration-server/node_modules/<package>/LICENSE*` files are authoritative
for installed versions. Distribution also preserves
`infra/licenses/notes-collaboration/jsondiffpatch.LICENSE` and
`infra/licenses/notes-collaboration/diff-match-patch.LICENSE`.

## Container and service selections

- `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`: uv is dual licensed
  Apache-2.0 OR MIT; the image also includes Python and Debian components under
  their own licenses.
- `debian:bookworm@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818`:
  Debian package copyright files under `/usr/share/doc/*/copyright` are
  authoritative for installed components.
- `node:22-alpine`: Node.js is MIT licensed; Alpine packages retain their
  individual licenses.
- `postgres:17`: PostgreSQL License.
- `redis:8.8.0-alpine`: Redis Open Source tri-license distribution; Atlas
  selects the AGPLv3 option for the unmodified internal broker image. Atlas
  does not offer Redis functionality as a managed service.
- `qdrant/qdrant:v1.18.1-unprivileged`: Apache-2.0.

The built Office renderer additionally contains LibreOffice, PDFium, DejaVu,
Noto, and Debian packages. See
`office-renderer/THIRD_PARTY_NOTICES.md` and preserve the image's installed
copyright files.

## Model asset

- `intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3`:
  MIT license in the pinned Hugging Face model card.

## Maintenance

Run:

```sh
infra/scripts/audit_third_party_notices
```

The audit compares direct Python manifests, Web runtime dependencies, static
base/service image references, and the pinned embedding model with this file.
Changing a dependency, image, model, extra, or binary source requires a fresh
license review. Passing the audit proves inventory coverage only; it does not
constitute legal approval.
