from __future__ import annotations

import pytest

from atlas_production.modules.notes.document_schema import (
    NOTE_DOCUMENT_SCHEMA_V2,
    NoteDocumentValidationError,
    validate_note_document,
)


def _paragraph(block_id: str, text: str = "Hello") -> dict[str, object]:
    return {
        "type": "paragraph",
        "attrs": {"block_id": block_id},
        "content": [{"type": "text", "text": text}],
    }


def test_closed_v2_schema_accepts_formats_table_and_opaque_note_image() -> None:
    body = {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"block_id": "block-heading", "level": 2},
                "content": [
                    {
                        "type": "text",
                        "text": "Atlas",
                        "marks": [
                            {"type": "bold"},
                            {"type": "underline"},
                            {
                                "type": "link",
                                "attrs": {
                                    "href": "https://atlas.invalid",
                                    "target": "_blank",
                                    "rel": "noopener noreferrer nofollow",
                                    "class": None,
                                    "title": None,
                                },
                            },
                        ],
                    }
                ],
            },
            {
                "type": "taskList",
                "attrs": {"block_id": "block-tasks"},
                "content": [
                    {
                        "type": "taskItem",
                        "attrs": {"checked": True},
                        "content": [_paragraph("nested-paragraph", "Done")],
                    }
                ],
            },
            {
                "type": "table",
                "attrs": {"block_id": "block-table"},
                "content": [
                    {
                        "type": "tableRow",
                        "content": [
                            {
                                "type": "tableHeader",
                                "attrs": {"colspan": 1, "rowspan": 1, "colwidth": [180]},
                                "content": [_paragraph("cell-heading", "Name")],
                            },
                            {
                                "type": "tableCell",
                                "attrs": {"colspan": 1, "rowspan": 1, "colwidth": [220]},
                                "content": [_paragraph("cell-value", "Atlas")],
                            },
                        ],
                    }
                ],
            },
            {
                "type": "noteImage",
                "attrs": {
                    "block_id": "block-image",
                    "attachment_ref": "natt-opaque",
                    "alt": "Screenshot",
                    "caption": "Current state",
                    "width": 1200,
                    "height": 800,
                },
            },
        ],
    }

    assert validate_note_document(body, NOTE_DOCUMENT_SCHEMA_V2) == frozenset(
        {"natt-opaque"}
    )


@pytest.mark.parametrize(
    "body",
    [
        {"type": "doc", "content": [_paragraph("duplicate"), _paragraph("duplicate")]},
        {"type": "doc", "content": [{"type": "paragraph", "content": []}]},
        {
            "type": "doc",
            "content": [
                {
                    "type": "noteImage",
                    "attrs": {
                        "block_id": "image",
                        "attachment_ref": "natt-1",
                        "src": "https://example.invalid/image.png",
                    },
                }
            ],
        },
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "attrs": {"block_id": "raw"},
                    "content": [
                        {"type": "text", "text": "data:image/png;base64,AAAA"}
                    ],
                }
            ],
            "raw_bytes": "AAAA",
        },
    ],
)
def test_closed_v2_schema_rejects_missing_duplicate_and_open_image_fields(
    body: dict[str, object],
) -> None:
    with pytest.raises(NoteDocumentValidationError):
        validate_note_document(body, NOTE_DOCUMENT_SCHEMA_V2)


def test_closed_v2_schema_rejects_old_or_unknown_schema() -> None:
    with pytest.raises(NoteDocumentValidationError):
        validate_note_document({"type": "doc", "content": []}, "tiptap-prosemirror-v1")


def test_closed_v2_schema_matches_tiptap_list_and_blockquote_parent_rules() -> None:
    accepted = {
        "type": "doc",
        "content": [{
            "type": "blockquote",
            "attrs": {"block_id": "quote-1"},
            "content": [{
                "type": "noteImage",
                "attrs": {
                    "block_id": "nested-image-1",
                    "attachment_ref": "natt-nested",
                    "alt": "",
                    "caption": "",
                    "width": 2,
                    "height": 2,
                },
            }],
        }],
    }
    assert validate_note_document(accepted, NOTE_DOCUMENT_SCHEMA_V2) == frozenset(
        {"natt-nested"}
    )

    heading_first_list = {
        "type": "doc",
        "content": [{
            "type": "bulletList",
            "attrs": {"block_id": "list-1"},
            "content": [{
                "type": "listItem",
                "content": [{
                    "type": "heading",
                    "attrs": {"block_id": "heading-1", "level": 2},
                    "content": [{"type": "text", "text": "Invalid first child"}],
                }],
            }],
        }],
    }
    with pytest.raises(NoteDocumentValidationError):
        validate_note_document(heading_first_list, NOTE_DOCUMENT_SCHEMA_V2)
