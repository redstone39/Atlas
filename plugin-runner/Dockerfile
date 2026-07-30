FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONPATH=/app/src

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY plugin-sdk/pyproject.toml /plugin-sdk/pyproject.toml
COPY plugin-runner/pyproject.toml plugin-runner/uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project --no-install-package atlas-processing-sdk
RUN uv run --no-sync docling-tools models download layout tableformer rapidocr --output-dir /opt/docling-models
COPY plugin-sdk /plugin-sdk
COPY plugin-runner/src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 atlasrunner && chown -R atlasrunner:atlasrunner /app
ENV ATLAS_DOCLING_ARTIFACTS_PATH=/opt/docling-models
USER atlasrunner
EXPOSE 8013
CMD ["/app/.venv/bin/uvicorn", "atlas_plugin_runner.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8013"]
