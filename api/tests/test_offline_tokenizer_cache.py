from __future__ import annotations

import os
from pathlib import Path

import pytest

from atlas_production.async_runtime import offline_tokenizer_cache


class _FakeEncoding:
    def encode(self, value: str) -> list[int]:
        return list(value.encode("utf-8"))

    def decode(self, value: list[int]) -> str:
        return bytes(value).decode("utf-8")


def test_prepare_tokenizer_cache_loads_every_supported_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        offline_tokenizer_cache,
        "SUPPORTED_TOKENIZER_PROFILES",
        ("cl100k_base", "o200k_base"),
    )

    def fake_get_encoding(profile: str) -> _FakeEncoding:
        observed.append((profile, os.environ["TIKTOKEN_CACHE_DIR"]))
        return _FakeEncoding()

    monkeypatch.setattr(offline_tokenizer_cache.tiktoken, "get_encoding", fake_get_encoding)

    result = offline_tokenizer_cache.prepare_tokenizer_cache(
        cache_dir=tmp_path / "cache",
        download=True,
    )

    assert observed == [
        ("cl100k_base", str(tmp_path / "cache")),
        ("o200k_base", str(tmp_path / "cache")),
    ]
    assert result == {
        "status": "succeeded",
        "profile_count": 2,
        "profiles": ["cl100k_base", "o200k_base"],
        "cache_file_count": 0,
        "cache_total_bytes": 0,
        "mode": "download_and_verify",
    }


def test_prepare_tokenizer_cache_offline_mode_denies_missing_asset_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        offline_tokenizer_cache,
        "SUPPORTED_TOKENIZER_PROFILES",
        ("o200k_base",),
    )

    def fake_get_encoding(_profile: str) -> _FakeEncoding:
        offline_tokenizer_cache.tiktoken_load.read_file("https://example.invalid/asset")
        return _FakeEncoding()

    monkeypatch.setattr(offline_tokenizer_cache.tiktoken, "get_encoding", fake_get_encoding)

    with pytest.raises(RuntimeError, match="offline_tokenizer_cache_incomplete"):
        offline_tokenizer_cache.prepare_tokenizer_cache(
            cache_dir=tmp_path / "cache",
            download=False,
        )


def test_prepare_tokenizer_cache_restores_process_state_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_cache = os.environ.get("TIKTOKEN_CACHE_DIR")
    original_reader = offline_tokenizer_cache.tiktoken_load.read_file
    monkeypatch.setattr(
        offline_tokenizer_cache,
        "SUPPORTED_TOKENIZER_PROFILES",
        ("broken",),
    )
    monkeypatch.setattr(
        offline_tokenizer_cache.tiktoken,
        "get_encoding",
        lambda _profile: (_ for _ in ()).throw(ValueError("broken profile")),
    )

    with pytest.raises(ValueError, match="broken profile"):
        offline_tokenizer_cache.prepare_tokenizer_cache(
            cache_dir=tmp_path / "cache",
            download=False,
        )

    assert os.environ.get("TIKTOKEN_CACHE_DIR") == original_cache
    assert offline_tokenizer_cache.tiktoken_load.read_file is original_reader
