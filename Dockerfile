# Toolchain image for MarketLens (Node + pnpm + uv). The repository is bind-mounted at /app by
# docker-compose.yml, and dependencies are installed into container-only volumes on first start.
FROM node:24-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && corepack enable

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    CI=true

WORKDIR /app
EXPOSE 3000 8000
