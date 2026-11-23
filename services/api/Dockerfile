# Build context: repository root (the API depends on the workspace lib taxi-contracts).
#   docker build -f services/api/Dockerfile -t taxi-api .
# The server is linux/amd64; CI builds that platform explicitly.

# ---------- builder: resolve and install into a self-contained venv ----------
# uv version kept equal to UV_VERSION in .github/workflows/ci.yml and the uv-lock hook rev.
FROM ghcr.io/astral-sh/uv:0.13.9-python3.13-trixie-slim@sha256:803d2b80b6913e562837d69f037f39bfea80f51ef3f0dd0d8fdffbd95688fd6d AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# 1) Third-party dependencies only: cached until uv.lock or a pyproject changes.
COPY pyproject.toml uv.lock ./
COPY libs/contracts/pyproject.toml libs/contracts/pyproject.toml
COPY libs/features/pyproject.toml libs/features/pyproject.toml
COPY services/api/pyproject.toml services/api/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package taxi-api --no-install-workspace

# 2) Our code, installed non-editable so the runtime image needs no source tree.
COPY libs/contracts libs/contracts
COPY libs/features libs/features
COPY services/api services/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package taxi-api --no-editable

# ---------- runtime: slim, non-root, no build tooling ----------
# Same Python minor and path (/usr/local/bin/python3.13) as the builder, so the venv works as-is.
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS runtime

LABEL org.opencontainers.image.title="taxi-api" \
      org.opencontainers.image.description="NYC taxi trip-duration prediction API" \
      org.opencontainers.image.source="https://github.com/shadmanArko/nyc-taxi-lakehouse-mlops" \
      org.opencontainers.image.licenses="MIT"

# Apply Debian security fixes published after the pinned base was built (the CI image
# scan fails on fixable high/critical CVEs), then create the unprivileged user.
# libgomp1: OpenMP runtime that LightGBM (the served model) links against.
# CI passes today's date: a changed build arg forces this layer to rebuild, so cached
# images still pick up security fixes published since the last build (at least daily).
ARG SECURITY_REFRESH=manual
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /models && chown 10001:10001 /models   # local model cache (a volume)

COPY --from=builder --chown=root:root /app/.venv /app/.venv

# HOME/XDG point at the tmpfs: the root filesystem is read-only at runtime.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

USER 10001:10001
WORKDIR /app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# 0.0.0.0 inside the container only; the host publishes it on 127.0.0.1 (see compose).
# Proxy headers are enabled in Phase 6 with FORWARDED_ALLOW_IPS set to the reverse proxy only.
CMD ["uvicorn", "taxi_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
