from __future__ import annotations

from pathlib import Path

import pytest

from atlas_production.async_runtime import offline_runtime_assets


def test_prepare_runtime_assets_verifies_embedding_and_tokenizer_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_embedding(*, cache_dir: Path, download: bool) -> dict[str, object]:
        observed["embedding"] = (cache_dir, download)
        return {"status": "succeeded", "kind": "embedding"}

    def fake_tokenizer(*, cache_dir: Path, download: bool) -> dict[str, object]:
        observed["tokenizer"] = (cache_dir, download)
        return {"status": "succeeded", "kind": "tokenizer"}

    monkeypatch.setattr(offline_runtime_assets, "prepare_embedding_cache", fake_embedding)
    monkeypatch.setattr(offline_runtime_assets, "prepare_tokenizer_cache", fake_tokenizer)

    result = offline_runtime_assets.prepare_runtime_assets(
        embedding_cache_dir=tmp_path / "embedding",
        tokenizer_cache_dir=tmp_path / "tokenizer",
        download=False,
    )

    assert observed == {
        "embedding": (tmp_path / "embedding", False),
        "tokenizer": (tmp_path / "tokenizer", False),
    }
    assert result == {
        "status": "succeeded",
        "mode": "offline_verify",
        "embedding": {"status": "succeeded", "kind": "embedding"},
        "tokenizer": {"status": "succeeded", "kind": "tokenizer"},
    }
