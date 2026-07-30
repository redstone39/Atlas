from atlas_processing_sdk import SourceRegionDraft


class Plugin:
    """Minimal built-in shape; the Production runner injects parsed pages."""

    async def parse(self, request, context):
        pages = await context.artifact_broker.parsed_pdf_pages(
            request.artifact_ref, request.unit_start, request.unit_end
        )
        for page_number, text_ref in pages:
            yield SourceRegionDraft(
                source_region_identity=f"page:{page_number}",
                region_kind="page",
                content_kind_hint="text" if text_ref else "unknown",
                element_kind_hint="page",
                locator_draft={"selector_kind": "page_region", "page_number": page_number},
                normalized_text_ref=text_ref,
            )
