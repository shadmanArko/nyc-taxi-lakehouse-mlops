# MLflow tracking server + registry, with the Postgres and S3 drivers the official
# image lacks, running as a non-root user.
FROM ghcr.io/mlflow/mlflow:v3.18.0@sha256:257204c77980b0f3450a26eb6089e487603fcdd4509529eadaf88f90669f5c13

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "psycopg2-binary==2.9.13" "boto3>=1.40,<2" \
    # newer setuptools vendors fixed jaraco.context and wheel (CI image scan)
        "setuptools>=80.10" \
    && groupadd --system --gid 10001 mlflow \
    && useradd --system --uid 10001 --gid mlflow --no-create-home --shell /usr/sbin/nologin mlflow \
    && mkdir -p /mlartifacts && chown 10001:10001 /mlartifacts

ENV HOME=/tmp
USER 10001:10001
EXPOSE 5000
