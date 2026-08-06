from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Mapping
from uuid import NAMESPACE_URL, uuid5

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType
from huggingface_hub import snapshot_download
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    PayloadSchemaType,
    VectorParams,
)
from qdrant_client.models import PointIdsList

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
VECTOR_DIMENSION = 384
MODEL_ALLOW_PATTERNS = (
    "1_Pooling/config.json",
    "config.json",
    "modules.json",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "onnx/config.json",
    "onnx/model.onnx",
    "onnx/special_tokens_map.json",
    "onnx/tokenizer.json",
    "onnx/tokenizer_config.json",
)
COLLECTION_NAME = "atlas_evidence_v1"
EMBEDDING_CONTRACT_VERSION = "fastembed-mean-normalized-v1"
INDEX_CONTRACT_VERSION = "atlas-qdrant-evidence-points-v1"
CHUNKING_CONTRACT_VERSION = "atlas-text-windows-v1"
NORMALIZATION_CONTRACT_VERSION = "atlas-canonical-text-v1"


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    chunk_id: str
    processing_revision_id: str
    index_generation_id: str


def _parent_locator(chunk: dict) -> dict[str, object]:
    locator = chunk.get("locator") or {}
    result: dict[str, object] = {"segment_id": chunk["segment_id"]}
    for key in (
        "selector_kind",
        "page_number",
        "slide_number",
        "sheet_name",
        "paragraph_index",
        "table_index",
        "relationship_id",
        "image_index",
        "anchor_row",
        "anchor_column",
    ):
        value = locator.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            result[key] = value
    return result


def _point_payload(index_generation_id: str, chunk: dict) -> dict[str, object]:
    locator = chunk.get("locator") or {}
    processing_revision_id = chunk.get("processing_revision_id")
    if not isinstance(processing_revision_id, str) or not processing_revision_id:
        raise ValueError("processing_revision_id is required for vector payloads")
    return {
        "index_generation_id": index_generation_id,
        "processing_revision_id": processing_revision_id,
        "chunk_id": chunk["chunk_id"],
        "document_id": chunk["document_id"],
        "content_fingerprint": chunk["content_fingerprint"],
        "document_format": locator.get("document_format"),
        "evidence_modality": locator.get("evidence_modality"),
        "preview_kind": locator.get("preview_kind"),
        "page_number": locator.get("page_number"),
        "parent_locator": _parent_locator(chunk),
        "image_digest": locator.get("image_digest"),
        "parser_id": locator.get("parser_id"),
        "parser_revision": locator.get("parser_revision"),
        "profile_id": locator.get("profile_id"),
        "profile_revision": locator.get("profile_revision"),
        "processor_id": locator.get("processor_id"),
        "processor_revision": locator.get("processor_revision"),
    }


class VectorIndex:
    def __init__(self) -> None:
        self.client = QdrantClient(
            url=os.getenv("ATLAS_QDRANT_URL", "http://qdrant:6333"),
            timeout=float(os.getenv("ATLAS_QDRANT_TIMEOUT_SECONDS", "30")),
        )
        self.embedding: TextEmbedding | None = None
        self._profile_value: dict[str, object] | None = None
        self._embedding_lock = Lock()
        self._collection_ready = False
        self._collection_lock = Lock()

    def _ensure_embedding(self) -> TextEmbedding:
        if self.embedding is not None and self._profile_value is not None:
            return self.embedding
        with self._embedding_lock:
            if self.embedding is not None and self._profile_value is not None:
                return self.embedding
            cache_dir = os.getenv(
                "ATLAS_FASTEMBED_CACHE", "/var/lib/atlas-fastembed"
            )
            model_path = snapshot_download(
                repo_id=MODEL_NAME,
                revision=MODEL_REVISION,
                cache_dir=cache_dir,
                allow_patterns=MODEL_ALLOW_PATTERNS,
                local_files_only=os.getenv("ATLAS_EMBEDDING_OFFLINE") == "true",
            )
            if not any(
                model["model"].lower() == MODEL_NAME.lower()
                for model in TextEmbedding.list_supported_models()
            ):
                TextEmbedding.add_custom_model(
                    model=MODEL_NAME,
                    pooling=PoolingType.MEAN,
                    normalization=True,
                    sources=ModelSource(hf=MODEL_NAME),
                    dim=VECTOR_DIMENSION,
                    model_file="onnx/model.onnx",
                    license="MIT",
                )
            candidate_embedding = TextEmbedding(
                model_name=MODEL_NAME,
                cache_dir=cache_dir,
                specific_model_path=model_path,
            )
            candidate_profile = self._profile(Path(model_path))
            # Publish the adapter and its immutable profile together.  A failed
            # download, constructor, or profile digest leaves no partial state,
            # so the next authorized request may retry initialization.
            self._profile_value = candidate_profile
            self.embedding = candidate_embedding
            return candidate_embedding

    @property
    def profile(self) -> dict[str, object]:
        self._ensure_embedding()
        assert self._profile_value is not None
        return self._profile_value

    @staticmethod
    def _profile(model_path: Path) -> dict[str, object]:
        files = sorted(
            path for path in model_path.rglob("*")
            if path.is_file() and ".git" not in path.parts
        )
        tree = hashlib.sha256()
        for path in files:
            relative = path.relative_to(model_path).as_posix().encode("utf-8")
            tree.update(relative + b"\0")
            tree.update(hashlib.sha256(path.read_bytes()).digest())
        onnx_path = model_path / "onnx" / "model.onnx"
        return {
            "model": MODEL_NAME,
            "revision": MODEL_REVISION,
            "dimension": VECTOR_DIMENSION,
            "model_sha256": tree.hexdigest(),
            "onnx_sha256": hashlib.sha256(onnx_path.read_bytes()).hexdigest(),
        }

    def ensure_collection(self) -> None:
        if self._collection_ready:
            return
        with self._collection_lock:
            if self._collection_ready:
                return
            if not self.client.collection_exists(COLLECTION_NAME):
                self.client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(
                        size=VECTOR_DIMENSION, distance=Distance.COSINE
                    ),
                )
            for field_name in (
                "document_id",
                "processing_revision_id",
                "index_generation_id",
            ):
                self.client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            self._collection_ready = True

    def upsert(self, index_generation_id: str, chunks: list[dict]) -> list[dict]:
        self.ensure_collection()
        embedding = self._ensure_embedding()
        vectors = list(
            embedding.embed(
                [f"passage: {chunk['normalized_text']}" for chunk in chunks]
            )
        )
        points: list[PointStruct] = []
        mappings: list[dict] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            values = vector.tolist()
            point_id = str(
                uuid5(NAMESPACE_URL, f"atlas:{index_generation_id}:{chunk['chunk_id']}")
            )
            payload = _point_payload(index_generation_id, chunk)
            payload_digest = hashlib.sha256(
                repr(sorted(payload.items())).encode("utf-8")
            ).hexdigest()
            vector_digest = hashlib.sha256(
                b"".join(float(value).hex().encode("ascii") + b"\n" for value in values)
            ).hexdigest()
            points.append(PointStruct(id=point_id, vector=values, payload=payload))
            mappings.append(
                {
                    "index_generation_id": index_generation_id,
                    "point_id": point_id,
                    "chunk_id": chunk["chunk_id"],
                    "payload_digest": payload_digest,
                    "vector_digest": vector_digest,
                    "created_at": datetime.now(timezone.utc),
                }
            )
        if points:
            self.client.upsert(
                collection_name=COLLECTION_NAME,
                points=points,
                wait=True,
            )
        return mappings

    def delete_points(self, point_ids: list[str]) -> None:
        if not point_ids or not self.client.collection_exists(COLLECTION_NAME):
            return
        self.client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=PointIdsList(points=point_ids),
            wait=True,
        )

    def verify_generation(
        self,
        *,
        collection_name: str,
        index_generation_id: str,
        processing_revision_id: str,
        expected_points: Mapping[str, str],
    ) -> bool:
        """Verify the exact staged Qdrant identity set without reading vectors."""
        if not expected_points or not self.client.collection_exists(collection_name):
            return False
        generation_filter = Filter(
            must=[
                FieldCondition(
                    key="index_generation_id",
                    match=MatchValue(value=index_generation_id),
                )
            ]
        )
        count = self.client.count(
            collection_name=collection_name,
            count_filter=generation_filter,
            exact=True,
        )
        if int(count.count) != len(expected_points):
            return False
        records = self.client.retrieve(
            collection_name=collection_name,
            ids=sorted(expected_points),
            with_payload=[
                "processing_revision_id",
                "index_generation_id",
                "chunk_id",
            ],
            with_vectors=False,
        )
        observed: dict[str, str] = {}
        for record in records:
            point_id = str(record.id)
            payload = record.payload
            if (
                point_id in observed
                or not isinstance(payload, dict)
                or payload.get("processing_revision_id")
                != processing_revision_id
                or payload.get("index_generation_id") != index_generation_id
                or not isinstance(payload.get("chunk_id"), str)
            ):
                return False
            observed[point_id] = str(payload["chunk_id"])
        return observed == dict(expected_points)

    def search(
        self,
        query_text: str,
        *,
        limit: int,
        revision_index_pairs: set[tuple[str, str]],
    ) -> list[str]:
        # Preserve the existing processing/search contract: an unpublished
        # collection has no searchable chunks. Discovery uses search_hits()
        # directly so it can distinguish an unavailable vector channel from
        # a valid empty result.
        if (
            limit <= 0
            or not revision_index_pairs
            or not self.client.collection_exists(COLLECTION_NAME)
        ):
            return []
        return [
            hit.chunk_id
            for hit in self.search_hits(
                query_text,
                limit=limit,
                revision_index_pairs=revision_index_pairs,
            )
        ]

    def search_hits(
        self,
        query_text: str,
        *,
        limit: int,
        revision_index_pairs: set[tuple[str, str]],
        timeout_seconds: float | None = None,
    ) -> list[VectorSearchHit]:
        if (
            limit <= 0
            or not revision_index_pairs
        ):
            return []
        if not self.client.collection_exists(COLLECTION_NAME):
            raise ConnectionError("Qdrant collection is unavailable")
        embedding = self._ensure_embedding()
        vector = next(embedding.query_embed([f"query: {query_text}"])).tolist()
        response = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            query_filter=Filter(
                should=[
                    Filter(
                        must=[
                            FieldCondition(
                                key="processing_revision_id",
                                match=MatchValue(value=revision_id),
                            ),
                            FieldCondition(
                                key="index_generation_id",
                                match=MatchValue(value=index_id),
                            ),
                        ]
                    )
                    for revision_id, index_id in sorted(revision_index_pairs)
                ]
            ),
            limit=limit,
            with_payload=[
                "chunk_id",
                "processing_revision_id",
                "index_generation_id",
            ],
            with_vectors=False,
            timeout=(None if timeout_seconds is None else max(1, int(timeout_seconds))),
        )
        hits: list[VectorSearchHit] = []
        for point in response.points:
            payload = point.payload
            if not isinstance(payload, dict):
                continue
            chunk_id = payload.get("chunk_id")
            revision_id = payload.get("processing_revision_id")
            index_id = payload.get("index_generation_id")
            if (
                not isinstance(chunk_id, str)
                or not isinstance(revision_id, str)
                or not isinstance(index_id, str)
                or (revision_id, index_id) not in revision_index_pairs
            ):
                continue
            hits.append(
                VectorSearchHit(
                    chunk_id=chunk_id,
                    processing_revision_id=revision_id,
                    index_generation_id=index_id,
                )
            )
        return hits
