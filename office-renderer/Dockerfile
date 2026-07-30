FROM debian:bookworm@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
      python3 python3-pip python3-venv \
      libreoffice-core=4:7.4.7-1+deb12u14 \
      libreoffice-writer=4:7.4.7-1+deb12u14 \
      libreoffice-impress=4:7.4.7-1+deb12u14 \
      libreoffice-calc=4:7.4.7-1+deb12u14 \
      fonts-dejavu-core=2.37-6 \
      fonts-noto-core=20201225-1 \
      fonts-noto-cjk=1:20220127+repack1-1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY office-renderer/pyproject.toml ./
COPY office-renderer/src ./src
RUN python3 -m venv /app/.venv \
    && /app/.venv/bin/pip install --no-cache-dir .
COPY office-renderer/THIRD_PARTY_NOTICES.md ./THIRD_PARTY_NOTICES.md

RUN useradd --create-home --uid 10002 atlasrenderer \
    && chown -R atlasrenderer:atlasrenderer /app
USER atlasrenderer
ENV PYTHONPATH=/app/src HOME=/tmp/atlas-renderer-home LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
EXPOSE 8014
CMD ["/app/.venv/bin/uvicorn", "atlas_office_renderer.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8014"]
