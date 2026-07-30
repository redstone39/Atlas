from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .offline_embedding_cache import prepare_embedding_cache
from .offline_tokenizer_cache import prepare_tokenizer_cache


def prepare_runtime_assets(
    *,
    embedding_cache_dir: Path,
    tokenizer_cache_dir: Path,
    download: bool,
) -> dict[str, Any]:
    embedding = prepare_embedding_cache(cache_dir=embedding_cache_dir, download=download)
    tokenizer = prepare_tokenizer_cache(cache_dir=tokenizer_cache_dir, download=download)
    return {
        "status": "succeeded",
        "mode": "download_and_verify" if download else "offline_verify",
        "embedding": embedding,
        "tokenizer": tokenizer,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    embedding_cache_dir = Path(
        os.getenv("ATLAS_FASTEMBED_CACHE", "/var/lib/atlas-fastembed")
    )
    tokenizer_cache_dir = Path(
        os.getenv("TIKTOKEN_CACHE_DIR", "/var/lib/atlas-tiktoken")
    )
    try:
        result = prepare_runtime_assets(
            embedding_cache_dir=embedding_cache_dir,
            tokenizer_cache_dir=tokenizer_cache_dir,
            download=args.download,
        )
    except Exception:
        print(
            json.dumps(
                {"status": "failed", "error_code": "offline_runtime_assets_invalid"}
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
