ACTIVE_CHANNEL_REGISTRIES = {
    "kpel-registry-v0.1": {
        "generic_text": "claim_grounding",
        "table": "claim_grounding",
        "ocr_text": "claim_grounding",
        "visual_semantics": "visual_inference",
        "image": "locator_context",
        "image_context": "locator_context",
        "figure_context": "locator_context",
        "slide_context": "locator_context",
    }
}
ACTIVE_TRAIT_REGISTRIES = {
    "kpel-registry-v0.1": frozenset({
        "has_normalized_text",
        "has_table_cell_text",
        "layout_only_table",
        "visual_context_only",
        "low_confidence_profile",
    })
}
PRE_KPEL_ELEMENT_HINTS = {"page", "slide", "paragraph", "table", "figure", "image", "image_region"}
ACTIVE_OUTPUT_CONTRACTS = {"eir-draft-v1"}
