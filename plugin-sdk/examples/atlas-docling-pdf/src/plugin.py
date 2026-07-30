from io import BytesIO
import os

from atlas_processing_sdk import CandidateDraft


def _preview_region(document, item, page_number: int, region_kind: str):
    provenance = next(
        (value for value in item.prov if value.page_no == page_number),
        None,
    )
    page = document.pages.get(page_number)
    if provenance is None or page is None or provenance.bbox is None:
        return None
    bbox = provenance.bbox.to_bottom_left_origin(page.size.height)
    return {
        "page_number": page_number,
        "region_kind": region_kind,
        "source_element_id": str(item.self_ref),
        "coordinate_system": "pdf_crop_box_relative_bottom_left",
        "rectangles": [[bbox.l, bbox.b, bbox.r, bbox.t]],
        "page_width": page.size.width,
        "page_height": page.size.height,
        "geometry_version": "docling-page-region-v1",
    }


class Plugin:
    """Docling-backed best-effort layout processor for Atlas PDF citations."""

    async def process(self, request, context):
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import DocItemLabel, TextItem

        source = await context.artifact_broker.read_bytes(request.artifact_ref)
        pipeline_options = PdfPipelineOptions(
            artifacts_path=os.environ.get("ATLAS_DOCLING_ARTIFACTS_PATH"),
            do_ocr=True,
            enable_remote_services=False,
            allow_external_plugins=False,
        )
        page_number = request.locator_draft.get("page_number")
        page_range = (page_number, page_number) if isinstance(page_number, int) else None
        converter = DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        })
        conversion = converter.convert(
            DocumentStream(name="document.pdf", stream=BytesIO(source)),
            **({"page_range": page_range} if page_range is not None else {}),
        )
        document = conversion.document

        for item, _level in document.iterate_items():
            if (
                not isinstance(item, TextItem)
                or item.label == DocItemLabel.CAPTION
                or not item.text.strip()
            ):
                continue
            item_page = next((value.page_no for value in item.prov), None)
            if not isinstance(item_page, int) or (
                isinstance(page_number, int) and item_page != page_number
            ):
                continue
            text_ref = context.artifact_broker.put_text(item.text.strip())
            preview = _preview_region(document, item, item_page, "paragraph")
            yield CandidateDraft(
                source_region_ids=(request.region_id,),
                channel_id="generic_text",
                output_contract_version="eir-draft-v1",
                candidate_payload_ref=text_ref,
                content_kind_hint="text",
                element_kind_hint="paragraph",
                preview_region=preview,
                quality_flag_refs=(
                    () if preview else ("pdf_preview_region_missing",)
                ),
            )

        for table_index, table in enumerate(document.tables, start=1):
            table_page = next((value.page_no for value in table.prov), None)
            if not isinstance(table_page, int) or (
                isinstance(page_number, int) and table_page != page_number
            ):
                continue
            rows: list[list[dict[str, str]]] = [
                [
                    {"cell_id": f"r{row + 1}c{column + 1}", "text": ""}
                    for column in range(table.data.num_cols)
                ]
                for row in range(table.data.num_rows)
            ]
            cell_bboxes: dict[str, list[float]] = {}
            page = document.pages.get(table_page)
            for cell in table.data.table_cells:
                cell_id = f"r{cell.start_row_offset_idx + 1}c{cell.start_col_offset_idx + 1}"
                if (
                    cell.start_row_offset_idx < len(rows)
                    and cell.start_col_offset_idx < len(rows[cell.start_row_offset_idx])
                ):
                    rows[cell.start_row_offset_idx][cell.start_col_offset_idx] = {
                        "cell_id": cell_id,
                        "text": cell.text.strip(),
                    }
                if cell.bbox is not None and page is not None:
                    bbox = cell.bbox.to_bottom_left_origin(page.size.height)
                    cell_bboxes[cell_id] = [bbox.l, bbox.b, bbox.r, bbox.t]
            table_markdown = table.export_to_markdown(document).strip()
            if not table_markdown or not cell_bboxes:
                continue
            table_ref = context.artifact_broker.put_text(table_markdown)
            preview = _preview_region(document, table, table_page, "table")
            yield CandidateDraft(
                source_region_ids=(request.region_id,),
                channel_id="table",
                output_contract_version="eir-draft-v1",
                candidate_payload_ref=table_ref,
                content_kind_hint="table",
                element_kind_hint="table",
                table_grid={"table_id": f"table-{table_index}", "rows": rows},
                cell_bboxes=cell_bboxes,
                preview_region=preview,
                quality_flag_refs=(
                    () if preview else ("pdf_preview_region_missing",)
                ),
            )

        for picture in document.pictures:
            picture_page = next((value.page_no for value in picture.prov), None)
            if not isinstance(picture_page, int) or (
                isinstance(page_number, int) and picture_page != page_number
            ):
                continue
            caption = picture.caption_text(document).strip()
            if not caption:
                continue
            caption_ref = context.artifact_broker.put_text(caption)
            preview = _preview_region(document, picture, picture_page, "figure")
            yield CandidateDraft(
                source_region_ids=(request.region_id,),
                channel_id="generic_text",
                output_contract_version="eir-draft-v1",
                candidate_payload_ref=caption_ref,
                content_kind_hint="text",
                element_kind_hint="image",
                preview_region=preview,
                quality_flag_refs=(
                    () if preview else ("pdf_preview_region_missing",)
                ),
            )
