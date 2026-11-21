# ADR-0005: SeaweedFS for self-hosted object storage

- **Status:** Accepted, deployed (2026-11-21)
- **Date:** 2026-11-21

## Context

DVC (data versions) and MLflow (model artifacts) need S3-compatible storage on the
single self-hosted `linux/amd64` server. It must run as one hardened container, be
actively maintained, and work with `dvc-s3`/boto3 and MLflow's S3 artifact store.

The default choice a few years ago, MinIO community edition, is no longer viable: its
GitHub repository is archived ("no longer maintained"), community images and binaries
stopped being published in 2025, and the admin console was removed.

## Options considered (as of November 2026)

| Option | Licence | Maturity | Single-node setup | Notes |
|---|---|---|---|---|
| **SeaweedFS** 4.54 | Apache-2.0 | Since 2012, weekly releases | One command (`weed server -s3`) | Versioning, multipart, presigned URLs |
| Garage 2.5 | AGPL-3.0 | Mature, small-cluster focus | Several steps (layout, keys) | Smallest footprint; no versioning |
| RustFS 1.0.6 | Apache-2.0 | GA only in Sept 2026 | One command, web console | Young |
| Ceph RGW | LGPL | Very mature | Heavy (MON/MGR/OSD/RGW) | Overkill for one node |
| MinIO CE | AGPL-3.0 | Archived | - | Rejected: unmaintained |

## Decision

SeaweedFS 4.54, single node, pinned by digest, published on `127.0.0.1:8333` (S3 API
only), run as uid 10001 with a read-only root filesystem and all capabilities dropped
([`infra/storage/compose.yaml`](../../infra/storage/compose.yaml)). Access uses
**scoped identities**: `dvc` can only touch bucket `taxi-dvc`, `mlflow` only
`taxi-mlflow`. Credentials live in a mounted secrets file on the server and in the
developer's local DVC config, never in Git.

## Consequences

- Verified locally before requesting deployment: the same compose and image ran healthy
  under full hardening, `dvc push` succeeded, cache and remote reported in sync, and wrong
  credentials were refused with 403.
- With boto3 ≥ 1.36, clients must set `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`
  and `AWS_RESPONSE_CHECKSUM_VALIDATION=when_required` for non-AWS stores.
- Deployed on the server after operator review: healthy, S3 on loopback only, anonymous and
  wrong-key requests refused (403), and the `dvc` identity cannot see `taxi-mlflow`. Clients
  reach it through the existing SSH tunnel; `dvc push` from the laptop reports in sync.
- Client credentials live in a named AWS profile (`~/.aws/credentials`, mode 600) referenced
  from `.dvc/config.local`; the checksum settings go in the same profile in `~/.aws/config`.
- Swapping to Garage or RustFS later changes only the compose file and remote endpoint.
