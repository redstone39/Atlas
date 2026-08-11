from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from huggingface_hub import snapshot_download

from .embedding_model_contract import (
    MODEL_ALLOW_PATTERNS,
    MODEL_CONTENT_DIGEST,
    MODEL_NAME,
    MODEL_REVISION,
)


def prepare_embedding_cache(*, cache_dir: Path, download: bool) -> dict[str, object]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    snapshot = Path(
        snapshot_download(
            repo_id=MODEL_NAME,
            revision=MODEL_REVISION,
            cache_dir=str(cache_dir),
            allow_patterns=MODEL_ALLOW_PATTERNS,
            local_files_only=not download,
        )
    )
    cache_root = cache_dir.resolve()
    digest = hashlib.sha256()
    total_bytes = 0
    for relative in MODEL_ALLOW_PATTERNS:
        path = snapshot / relative
        if not path.is_file() or not path.resolve().is_relative_to(cache_root):
            raise RuntimeError("offline_embedding_cache_incomplete")
        content = path.read_bytes()
        total_bytes += len(content)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    content_digest = digest.hexdigest()
    if content_digest != MODEL_CONTENT_DIGEST:
        raise RuntimeError("offline_embedding_cache_digest_mismatch")
    return {
        "status": "succeeded",
        "model": MODEL_NAME,
        "revision": MODEL_REVISION,
        "file_count": len(MODEL_ALLOW_PATTERNS),
        "total_bytes": total_bytes,
        "content_digest": content_digest,
        "mode": "download_and_verify" if download else "offline_verify",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    cache_dir = Path(os.getenv("ATLAS_FASTEMBED_CACHE", "/var/lib/atlas-fastembed"))
    try:
        result = prepare_embedding_cache(cache_dir=cache_dir, download=args.download)
    except Exception:
        print(json.dumps({"status": "failed", "error_code": "offline_embedding_cache_invalid"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
