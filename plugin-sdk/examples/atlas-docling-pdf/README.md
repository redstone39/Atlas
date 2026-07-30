# atlas-docling-pdf installable processor descriptor

Official installable Docling-backed region processor for the
`atlas-docling-cpu-v1` runtime profile. The package itself carries no runtime
container; Atlas supplies the pinned Docling runtime. Release signing requires
the pinned `requirements.lock`, SPDX SBOM, fixture and conformance checks.

Version `0.2.0` enables local Docling OCR/layout and emits validated
`preview_region` geometry for extracted paragraphs, whole tables, and
caption-grounded pictures. It never calls remote services. Missing text or
geometry is an expected best-effort outcome and degrades to Atlas's verified
single-page citation preview.
