from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from atlas_production.async_runtime import offline_embedding_cache
from atlas_production.async_runtime.embedding_model_contract import MODEL_ALLOW_PATTERNS


def _write_snapshot(cache_dir: Path) -> Path:
    snapshot = cache_dir / "models--intfloat--multilingual-e5-small" / "snapshots" / "pinned"
    for index, relative in enumerate(MODEL_ALLOW_PATTERNS):
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture-{index}".encode())
    return snapshot


def _fixture_digest(snapshot: Path) -> str:
    digest = hashlib.sha256()
    for relative in MODEL_ALLOW_PATTERNS:
        content = (snapshot / relative).read_bytes()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def test_prepare_embedding_cache_verifies_complete_local_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    snapshot = _write_snapshot(cache_dir)
    observed: dict[str, object] = {}

    def fake_snapshot_download(**kwargs: object) -> str:
        observed.update(kwargs)
        return str(snapshot)

    monkeypatch.setattr(offline_embedding_cache, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr(
        offline_embedding_cache,
        "MODEL_CONTENT_DIGEST",
        _fixture_digest(snapshot),
    )

    result = offline_embedding_cache.prepare_embedding_cache(
        cache_dir=cache_dir,
        download=False,
    )

    assert observed["local_files_only"] is True
    assert observed["allow_patterns"] == MODEL_ALLOW_PATTERNS
    assert result["status"] == "succeeded"
    assert result["mode"] == "offline_verify"
    assert result["file_count"] == len(MODEL_ALLOW_PATTERNS)
    assert "cache" not in result


def test_prepare_embedding_cache_download_mode_uses_same_pinned_file_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    snapshot = _write_snapshot(cache_dir)
    observed: dict[str, object] = {}

    def fake_snapshot_download(**kwargs: object) -> str:
        observed.update(kwargs)
        return str(snapshot)

    monkeypatch.setattr(offline_embedding_cache, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr(
        offline_embedding_cache,
        "MODEL_CONTENT_DIGEST",
        _fixture_digest(snapshot),
    )

    result = offline_embedding_cache.prepare_embedding_cache(
        cache_dir=cache_dir,
        download=True,
    )

    assert observed["local_files_only"] is False
    assert observed["revision"] == offline_embedding_cache.MODEL_REVISION
    assert result["mode"] == "download_and_verify"


def test_prepare_embedding_cache_rejects_incomplete_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    snapshot = _write_snapshot(cache_dir)
    (snapshot / MODEL_ALLOW_PATTERNS[-1]).unlink()
    monkeypatch.setattr(
        offline_embedding_cache,
        "snapshot_download",
        lambda **_: str(snapshot),
    )

    with pytest.raises(RuntimeError, match="offline_embedding_cache_incomplete"):
        offline_embedding_cache.prepare_embedding_cache(
            cache_dir=cache_dir,
            download=False,
        )


def test_prepare_embedding_cache_rejects_altered_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    snapshot = _write_snapshot(cache_dir)
    monkeypatch.setattr(
        offline_embedding_cache,
        "snapshot_download",
        lambda **_: str(snapshot),
    )

    with pytest.raises(RuntimeError, match="offline_embedding_cache_digest_mismatch"):
        offline_embedding_cache.prepare_embedding_cache(
            cache_dir=cache_dir,
            download=False,
        )
