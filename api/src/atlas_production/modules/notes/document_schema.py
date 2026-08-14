"""Closed canonical document contract for collaborative Notes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


NOTE_DOCUMENT_SCHEMA_V2 = "tiptap-prosemirror-v2"
MAX_BLOCK_ID_LENGTH = 200
MAX_ATTACHMENT_REF_LENGTH = 200
MAX_ALT_LENGTH = 2_000
MAX_CAPTION_LENGTH = 10_000
MAX_LINK_LENGTH = 2_000
MAX_DOCUMENT_DEPTH = 64


class NoteDocumentValidationError(ValueError):
    """Raised when canonical Notes JSON is outside the closed v2 schema."""


_CONTENT_NODES = {
    "doc",
    "paragraph",
    "heading",
    "blockquote",
    "codeBlock",
    "bulletList",
    "orderedList",
    "taskList",
    "listItem",
    "taskItem",
    "table",
    "tableRow",
    "tableHeader",
    "tableCell",
}
_LEAF_NODES = {"text", "horizontalRule", "noteImage"}
_TOP_LEVEL_NODES = {
    "paragraph",
    "heading",
    "blockquote",
    "codeBlock",
    "bulletList",
    "orderedList",
    "taskList",
    "horizontalRule",
    "table",
    "noteImage",
}
_MARKS = {"bold", "italic", "underline", "strike", "code", "link"}
_NODE_ATTRS: dict[str, set[str]] = {
    "paragraph": {"block_id"},
    "heading": {"block_id", "level"},
    "blockquote": {"block_id"},
    "codeBlock": {"block_id", "language"},
    "bulletList": {"block_id"},
    "orderedList": {"block_id", "start"},
    "taskList": {"block_id"},
    "listItem": set(),
    "taskItem": {"checked"},
    "horizontalRule": {"block_id"},
    "table": {"block_id"},
    "tableRow": set(),
    "tableHeader": {"colspan", "rowspan", "colwidth"},
    "tableCell": {"colspan", "rowspan", "colwidth"},
    "noteImage": {
        "block_id",
        "attachment_ref",
        "alt",
        "caption",
        "width",
        "height",
    },
}
_CHILD_TYPES: dict[str, set[str]] = {
    "paragraph": {"text"},
    "heading": {"text"},
    "codeBlock": {"text"},
    "blockquote": _TOP_LEVEL_NODES,
    "bulletList": {"listItem"},
    "orderedList": {"listItem"},
    "taskList": {"taskItem"},
    "listItem": _TOP_LEVEL_NODES | {"listItem", "taskItem"},
    "taskItem": _TOP_LEVEL_NODES | {"listItem", "taskItem"},
    "table": {"tableRow"},
    "tableRow": {"tableHeader", "tableCell"},
    "tableHeader": _TOP_LEVEL_NODES - {"noteImage", "table"},
    "tableCell": _TOP_LEVEL_NODES - {"noteImage", "table"},
}


def _fail(path: tuple[int, ...], message: str) -> None:
    rendered = "root" if not path else "root.content." + ".content.".join(map(str, path))
    raise NoteDocumentValidationError(f"{rendered}: {message}")


def _mapping(value: object, path: tuple[int, ...], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail(path, f"{label} must be an object")
    return value


def _content(value: object, path: tuple[int, ...]) -> Sequence[object]:
    if not isinstance(value, list):
        _fail(path, "content must be an array")
    return value


def _bounded_string(
    value: object,
    path: tuple[int, ...],
    label: str,
    *,
    maximum: int,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        _fail(path, f"{label} must be a string of at most {maximum} characters")
    return value


def _validate_attrs(node_type: str, attrs_value: object, path: tuple[int, ...]) -> Mapping[str, Any]:
    attrs = _mapping(attrs_value, path, "attrs")
    unknown = set(attrs) - _NODE_ATTRS.get(node_type, set())
    if unknown:
        _fail(path, f"unsupported {node_type} attrs: {sorted(unknown)}")

    if "block_id" in attrs:
        _bounded_string(
            attrs["block_id"], path, "block_id", maximum=MAX_BLOCK_ID_LENGTH, allow_empty=False
        )
    if node_type == "heading" and attrs.get("level") not in {1, 2, 3}:
        _fail(path, "heading level must be 1, 2, or 3")
    if node_type == "orderedList" and (
        isinstance(attrs.get("start", 1), bool)
        or not isinstance(attrs.get("start", 1), int)
        or attrs.get("start", 1) < 1
    ):
        _fail(path, "orderedList start must be a positive integer")
    if node_type == "taskItem" and not isinstance(attrs.get("checked", False), bool):
        _fail(path, "taskItem checked must be boolean")
    if node_type == "codeBlock" and attrs.get("language") is not None:
        _bounded_string(attrs["language"], path, "language", maximum=100)
    if node_type in {"tableHeader", "tableCell"}:
        for key in ("colspan", "rowspan"):
            value = attrs.get(key, 1)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                _fail(path, f"{key} must be a positive integer")
        colwidth = attrs.get("colwidth")
        if colwidth is not None and (
            not isinstance(colwidth, list)
            or not colwidth
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in colwidth)
        ):
            _fail(path, "colwidth must be null or a non-empty positive integer array")
        if isinstance(colwidth, list) and len(colwidth) != attrs.get("colspan", 1):
            _fail(path, "colwidth length must equal colspan")
    if node_type == "noteImage":
        _bounded_string(
            attrs.get("attachment_ref"),
            path,
            "attachment_ref",
            maximum=MAX_ATTACHMENT_REF_LENGTH,
            allow_empty=False,
        )
        for key, maximum in (("alt", MAX_ALT_LENGTH), ("caption", MAX_CAPTION_LENGTH)):
            if attrs.get(key) is not None:
                _bounded_string(attrs[key], path, key, maximum=maximum)
        for key in ("width", "height"):
            value = attrs.get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                _fail(path, f"{key} must be null or a positive integer")
    return attrs


def _validate_marks(value: object, path: tuple[int, ...]) -> None:
    if not isinstance(value, list):
        _fail(path, "marks must be an array")
    seen: set[str] = set()
    for mark_value in value:
        mark = _mapping(mark_value, path, "mark")
        if set(mark) - {"type", "attrs"}:
            _fail(path, "mark contains unsupported fields")
        mark_type = mark.get("type")
        if mark_type not in _MARKS or mark_type in seen:
            _fail(path, "mark type is unsupported or duplicated")
        seen.add(mark_type)
        attrs = mark.get("attrs", {})
        if mark_type != "link":
            if attrs not in ({}, None):
                _fail(path, f"{mark_type} does not accept attrs")
            continue
        link_attrs = _mapping(attrs, path, "link attrs")
        if set(link_attrs) - {"href", "target", "rel", "class", "title"}:
            _fail(path, "link contains unsupported attrs")
        href = _bounded_string(
            link_attrs.get("href"), path, "href", maximum=MAX_LINK_LENGTH, allow_empty=False
        )
        if not href.startswith(("http://", "https://", "mailto:", "tel:", "/", "#")):
            _fail(path, "link href uses an unsupported scheme")
        for key in ("target", "rel", "class", "title"):
            if link_attrs.get(key) is not None:
                _bounded_string(link_attrs[key], path, key, maximum=200)


def _validate_node(
    value: object,
    path: tuple[int, ...],
    *,
    top_level: bool,
    block_ids: set[str],
    attachment_refs: set[str],
    depth: int,
) -> None:
    if depth > MAX_DOCUMENT_DEPTH:
        _fail(path, "document nesting is too deep")
    node = _mapping(value, path, "node")
    node_type = node.get("type")
    if not isinstance(node_type, str) or node_type not in _CONTENT_NODES | _LEAF_NODES:
        _fail(path, "node type is unsupported")

    allowed_fields = {"type", "attrs", "content"}
    if node_type == "text":
        allowed_fields = {"type", "text", "marks"}
    if set(node) - allowed_fields:
        _fail(path, f"{node_type} contains unsupported fields")

    if node_type == "text":
        _bounded_string(node.get("text"), path, "text", maximum=1_048_576, allow_empty=False)
        if "marks" in node:
            _validate_marks(node["marks"], path)
        return

    attrs: Mapping[str, Any] = {}
    if "attrs" in node:
        attrs = _validate_attrs(node_type, node["attrs"], path)
    elif top_level or node_type == "noteImage":
        _fail(path, f"{node_type} attrs are required")

    if top_level:
        if node_type not in _TOP_LEVEL_NODES:
            _fail(path, f"{node_type} is not a top-level block")
        block_id = attrs.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            _fail(path, "top-level block_id is required")
        if block_id in block_ids:
            _fail(path, "top-level block_id is duplicated")
        block_ids.add(block_id)
    if node_type == "noteImage":
        attachment_refs.add(str(attrs["attachment_ref"]))

    if node_type in _LEAF_NODES:
        if "content" in node:
            _fail(path, f"{node_type} must not contain content")
        return

    children = _content(node.get("content", []), path)
    if node_type in {"listItem", "taskItem"} and (
        not children
        or not isinstance(children[0], Mapping)
        or children[0].get("type") != "paragraph"
    ):
        _fail(path, f"{node_type} must begin with a paragraph")
    allowed_children = _CHILD_TYPES[node_type]
    for index, child in enumerate(children):
        if not isinstance(child, Mapping) or child.get("type") not in allowed_children:
            _fail((*path, index), f"{node_type} contains an unsupported child")
        _validate_node(
            child,
            (*path, index),
            top_level=False,
            block_ids=block_ids,
            attachment_refs=attachment_refs,
            depth=depth + 1,
        )


def validate_note_document(
    canonical_body: object,
    document_schema: str,
) -> frozenset[str]:
    """Validate v2 JSON and return every referenced exact-note attachment."""

    if document_schema != NOTE_DOCUMENT_SCHEMA_V2:
        raise NoteDocumentValidationError(
            f"document_schema must equal {NOTE_DOCUMENT_SCHEMA_V2}"
        )
    root = _mapping(canonical_body, (), "canonical_body")
    if set(root) - {"type", "content"} or root.get("type") != "doc":
        _fail((), "canonical body must be a doc with only type/content fields")
    children = _content(root.get("content", []), ())
    block_ids: set[str] = set()
    attachment_refs: set[str] = set()
    for index, child in enumerate(children):
        _validate_node(
            child,
            (index,),
            top_level=True,
            block_ids=block_ids,
            attachment_refs=attachment_refs,
            depth=1,
        )
    return frozenset(attachment_refs)


__all__ = [
    "NOTE_DOCUMENT_SCHEMA_V2",
    "NoteDocumentValidationError",
    "validate_note_document",
]
