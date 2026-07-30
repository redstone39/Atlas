from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any

import tiktoken
import tiktoken.load as tiktoken_load
import tiktoken.registry as tiktoken_registry


SUPPORTED_TOKENIZER_PROFILES = tuple(tiktoken.list_encoding_names())
_VERIFICATION_TEXT = "Atlas offline tokenizer verification"
_CACHE_PREPARATION_LOCK = Lock()


def _offline_read_denied(_blobpath: str) -> bytes:
    raise RuntimeError("offline_tokenizer_cache_incomplete")


def _cache_summary(cache_dir: Path) -> tuple[int, int]:
    files = sorted(path for path in cache_dir.iterdir() if path.is_file())
    return len(files), sum(path.stat().st_size for path in files)


def prepare_tokenizer_cache(*, cache_dir: Path, download: bool) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)

    with _CACHE_PREPARATION_LOCK:
        previous_cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR")
        previous_reader = tiktoken_load.read_file
        previous_encodings = dict(tiktoken_registry.ENCODINGS)
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)
        if not download:
            tiktoken_load.read_file = _offline_read_denied
        tiktoken_registry.ENCODINGS.clear()
        try:
            for profile in SUPPORTED_TOKENIZER_PROFILES:
                encoding = tiktoken.get_encoding(profile)
                tokens = encoding.encode(_VERIFICATION_TEXT)
                if not tokens or encoding.decode(tokens) != _VERIFICATION_TEXT:
                    raise RuntimeError("offline_tokenizer_cache_invalid")
        finally:
            tiktoken_registry.ENCODINGS.clear()
            tiktoken_registry.ENCODINGS.update(previous_encodings)
            tiktoken_load.read_file = previous_reader
            if previous_cache_dir is None:
                os.environ.pop("TIKTOKEN_CACHE_DIR", None)
            else:
                os.environ["TIKTOKEN_CACHE_DIR"] = previous_cache_dir

    file_count, total_bytes = _cache_summary(cache_dir)
    return {
        "status": "succeeded",
        "profile_count": len(SUPPORTED_TOKENIZER_PROFILES),
        "profiles": list(SUPPORTED_TOKENIZER_PROFILES),
        "cache_file_count": file_count,
        "cache_total_bytes": total_bytes,
        "mode": "download_and_verify" if download else "offline_verify",
    }
