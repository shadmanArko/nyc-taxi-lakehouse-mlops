# Airflow 3.4 + our job CLIs in a separate virtualenv.
# Build context: repository root.
#   docker build -f orchestration/airflow/Dockerfile -t taxi-airflow .

FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

# ---------- jobs: ingest, export-gold, train, portal - isolated from Airflow ----------
FROM apache/airflow:slim-3.4.0-python3.13@sha256:371c258d67cd0a34bac60a28c940b65968b98dff2554e12b981f1df75b4be570 AS jobs
# Build-only stage (never run), so root is fine here; the runtime stage ends as uid 50000.
# hadolint ignore=DL3002
USER 0
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/taxi-jobs
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY libs/contracts libs/contracts
COPY libs/features libs/features
COPY jobs/ingest jobs/ingest
COPY jobs/export_gold jobs/export_gold
COPY jobs/train jobs/train
COPY jobs/portal jobs/portal
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
        --package taxi-ingest --package taxi-export-gold --package taxi-train \
        --package taxi-portal

# ---------- runtime ----------
FROM apache/airflow:slim-3.4.0-python3.13@sha256:371c258d67cd0a34bac60a28c940b65968b98dff2554e12b981f1df75b4be570

LABEL org.opencontainers.image.title="taxi-airflow" \
      org.opencontainers.image.source="https://github.com/shadmanArko/nyc-taxi-lakehouse-mlops" \
      org.opencontainers.image.licenses="MIT"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
USER 0
# Security fixes published after the pinned base (the CI image scan enforces this).
# This base (a Docker Hardened Image) also keeps per-package records in
# /var/lib/dpkg/status.d/, which apt-get upgrade does not update. Drop a record when its
# version no longer matches what dpkg actually installed, so scanners see the truth.
# The base pins its hardened-image apt repo (dhi.io) at priority 999, above Debian's 500,
# so a newer *Debian* security fix (e.g. libc deb13u5) would never be taken. Lower that pin:
# a newer Debian release now wins, and since priority stays < 1000 nothing is downgraded.
# libgomp1: OpenMP runtime that LightGBM links against (training job), pinned to the exact
# build of the installed gcc-14-base it depends on.
# CI passes today's date: a changed build arg forces this layer to rebuild, so cached
# images still pick up security fixes published since the last build (at least daily).
ARG SECURITY_REFRESH=manual
RUN printf 'Package: *\nPin: origin dhi.io\nPin-Priority: 400\n' \
        > /etc/apt/preferences.d/90-prefer-newer-debian-security \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        "libgomp1=$(dpkg-query -W -f='${Version}' gcc-14-base)" \
    && rm -rf /var/lib/apt/lists/* \
    && for record in /var/lib/dpkg/status.d/*; do \
         pkg="$(sed -n 's/^Package: //p' "$record" | head -n1)"; \
         recorded="$(sed -n 's/^Version: //p' "$record" | head -n1)"; \
         installed="$(dpkg-query -W -f='${Version}' "$pkg" 2>/dev/null || true)"; \
         if [ -n "$installed" ] && [ "$installed" != "$recorded" ]; then rm -f "$record"; fi; \
       done
COPY --from=jobs --chown=root:root /opt/taxi-jobs /opt/taxi-jobs
# Downloads land here; a new named volume inherits this ownership (airflow = uid 50000).
RUN mkdir -p /opt/airflow/data && chown 50000:0 /opt/airflow/data

USER 50000
# Providers pinned by Airflow's own tested constraints for this exact release.
ARG AIRFLOW_CONSTRAINTS=https://raw.githubusercontent.com/apache/airflow/constraints-3.4.0/constraints-3.13.txt
RUN pip install --no-cache-dir \
        "apache-airflow[postgres]==3.4.0" \
        "apache-airflow-providers-databricks==7.23.0" \
        "apache-airflow-providers-standard==1.21.0" \
        --constraint "${AIRFLOW_CONSTRAINTS}" \
    # Security fixes newer than the constraints pin (CI image scan: GHSA-vvf4-9x63-8xvv).
    && pip install --no-cache-dir "dnspython>=2.9.2"

COPY --chown=50000:0 orchestration/airflow/dags /opt/airflow/dags
