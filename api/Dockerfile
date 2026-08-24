FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS api-dependencies

WORKDIR /app

ENV PYTHONPATH=/app/src \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY plugin-sdk /plugin-sdk
COPY api/pyproject.toml api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM api-dependencies AS tokenizer-assets

ENV TIKTOKEN_CACHE_DIR=/var/lib/atlas-tiktoken

COPY api/src/atlas_production/async_runtime/offline_tokenizer_cache.py \
    ./src/atlas_production/async_runtime/offline_tokenizer_cache.py
RUN --mount=type=cache,id=atlas-tiktoken-build,target=/var/cache/atlas-tiktoken \
    TIKTOKEN_CACHE_DIR=/var/cache/atlas-tiktoken \
    /app/.venv/bin/python -m atlas_production.async_runtime.offline_tokenizer_cache --download \
    && mkdir -p /var/lib/atlas-tiktoken \
    && cp -a /var/cache/atlas-tiktoken/. /var/lib/atlas-tiktoken/
RUN HF_HUB_OFFLINE=1 \
    /app/.venv/bin/python -m atlas_production.async_runtime.offline_tokenizer_cache

FROM api-dependencies AS embedding-model

ENV ATLAS_FASTEMBED_CACHE=/var/lib/atlas-fastembed \
    ATLAS_EMBEDDING_OFFLINE=true \
    HF_HOME=/var/lib/atlas-fastembed/huggingface \
    HF_HUB_CACHE=/var/lib/atlas-fastembed/huggingface/hub \
    HF_XET_CACHE=/var/lib/atlas-fastembed/huggingface/xet \
    XDG_CACHE_HOME=/var/lib/atlas-fastembed/xdg \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_OFFLINE=1

COPY api/src/atlas_production/__init__.py ./src/atlas_production/__init__.py
COPY api/src/atlas_production/async_runtime/__init__.py \
    ./src/atlas_production/async_runtime/__init__.py
COPY api/src/atlas_production/async_runtime/embedding_model_contract.py \
    ./src/atlas_production/async_runtime/embedding_model_contract.py
COPY api/src/atlas_production/async_runtime/offline_embedding_cache.py \
    ./src/atlas_production/async_runtime/offline_embedding_cache.py
RUN HF_HUB_OFFLINE=0 /app/.venv/bin/python -m atlas_production.async_runtime.offline_embedding_cache --download
RUN /app/.venv/bin/python -m atlas_production.async_runtime.offline_embedding_cache

CMD ["/app/.venv/bin/python", "-m", "atlas_production.async_runtime.offline_embedding_cache"]

FROM api-dependencies AS api

ENV ATLAS_FASTEMBED_CACHE=/var/lib/atlas-fastembed \
    ATLAS_EMBEDDING_OFFLINE=true \
    HF_HOME=/var/lib/atlas-fastembed/huggingface \
    HF_HUB_CACHE=/var/lib/atlas-fastembed/huggingface/hub \
    HF_XET_CACHE=/var/lib/atlas-fastembed/huggingface/xet \
    XDG_CACHE_HOME=/var/lib/atlas-fastembed/xdg \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_OFFLINE=1 \
    TIKTOKEN_CACHE_DIR=/var/lib/atlas-tiktoken

COPY --from=tokenizer-assets /var/lib/atlas-tiktoken /var/lib/atlas-tiktoken
COPY api/src ./src
COPY --chmod=755 infra/docker-entrypoint.resolve-secrets /usr/local/bin/docker-entrypoint.resolve-secrets
COPY contracts /contracts

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.resolve-secrets"]
EXPOSE 8002

CMD ["/app/.venv/bin/uvicorn", "atlas_production.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8002"]
