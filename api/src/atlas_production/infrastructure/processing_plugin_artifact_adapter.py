from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4


class LocalProcessingPluginArtifactStore:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or os.getenv("ATLAS_PROCESSING_PLUGIN_ARTIFACT_ROOT", "/tmp/atlas-processing-plugins"))

    def put(self, payload: bytes) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        artifact_id = f"plugin-artifact-{uuid4().hex}"
        (self.root / artifact_id).write_bytes(payload)
        return artifact_id

    def get(self, artifact_ref: str) -> bytes:
        self._validate_ref(artifact_ref)
        return (self.root / artifact_ref).read_bytes()

    def delete(self, artifact_ref: str) -> None:
        """Compensate a metadata transaction that failed after artifact creation."""
        self._validate_ref(artifact_ref)
        (self.root / artifact_ref).unlink(missing_ok=True)

    @staticmethod
    def _validate_ref(artifact_ref: str) -> None:
        if not artifact_ref.startswith("plugin-artifact-") or "/" in artifact_ref or "\\" in artifact_ref:
            raise ValueError("invalid opaque plugin artifact reference")
