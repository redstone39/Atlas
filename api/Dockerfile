FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONPATH=/app/src \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY plugin-sdk /plugin-sdk
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev

COPY api/src ./src
COPY contracts /contracts

ENV ATLAS_FASTEMBED_CACHE=/var/lib/atlas-fastembed \
    HF_HOME=/var/lib/atlas-fastembed/huggingface \
    HF_HUB_CACHE=/var/lib/atlas-fastembed/huggingface/hub \
    HF_XET_CACHE=/var/lib/atlas-fastembed/huggingface/xet \
    XDG_CACHE_HOME=/var/lib/atlas-fastembed/xdg \
    HF_HUB_DISABLE_XET=1 \
    TIKTOKEN_CACHE_DIR=/var/lib/atlas-tiktoken
RUN /app/.venv/bin/python -m atlas_production.async_runtime.offline_runtime_assets --download
RUN HF_HUB_OFFLINE=1 /app/.venv/bin/python -m atlas_production.async_runtime.offline_runtime_assets

EXPOSE 8002

CMD ["/app/.venv/bin/uvicorn", "atlas_production.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8002"]
