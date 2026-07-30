"""Public Atlas processing plugin SDK."""

from .contracts import (
    BaseParserPlugin,
    CandidateDraft,
    PluginContext,
    RegionInput,
    RegionKind,
    RegionProcessorPlugin,
    ParserInput,
    SourceRegionDraft,
    ContentKindHint,
    validate_plugin_output_payload,
    validate_preview_region,
)

__all__ = [
    "BaseParserPlugin",
    "CandidateDraft",
    "PluginContext",
    "RegionInput",
    "RegionKind",
    "RegionProcessorPlugin",
    "ParserInput",
    "SourceRegionDraft",
    "ContentKindHint",
    "validate_plugin_output_payload",
    "validate_preview_region",
]

__version__ = "0.1.4"
