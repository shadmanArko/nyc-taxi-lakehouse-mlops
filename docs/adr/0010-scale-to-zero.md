# ADR-0010: Scale-to-zero on a single server (Sablier), public via Tailscale Funnel

- **Status:** Accepted (server deployment pending operator approval)
- **Date:** 2026-11-23

## Context

The project runs on a private home server that hosts other workloads. It will be used
rarely (a portfolio demo), so the owner wants it to cost ~nothing while idle and to wake
when someone visits: "serverless" behaviour without a cloud provider or Kubernetes.

## Decision

**Sablier + a tiny always-on gateway.** Caddy (with the Sablier plugin, compiled in with
xcaddy) is the only always-on HTTP component. Containers carry labels
`sablier.enable=true` / `sablier.group=<group>`; Sablier starts a group when a request
arrives and stops it after 15 idle minutes.

| Group | Members | Woken by | Waiting behaviour |
|---|---|---|---|
| `taxi-serving` | web, api | a visitor (`:8090`) | browsers: "waking up" page; API calls: block up to 90 s |
| `taxi-mlflow` | mlflow, mlflow-db | API model refresh, Airflow training, admin UI (`:8091`) | block up to 120 s |
| (systemd timer) | Airflow | the 15th of each month | script wakes it, runs the pipeline, always stops it |

**Fast cold starts.** The API keeps the served model on a persistent volume and loads it
at once on wake (`warm_start`), without MLflow; the background refresh then checks the
registry through the gateway (waking MLflow, not the visitor). Health checks probe every
second while starting (`start_interval: 1s`), because Sablier releases a request only when
the group is healthy. Measured on a laptop: 39 s cold start before these two changes,
**3.3-3.5 s** after (three runs); warm requests ~10 ms.

**Least privilege for container control.** Starting/stopping containers needs the Docker
API, and the Docker socket is root-equivalent. Only a socket proxy mounts it (read-only);
it allows list/inspect/events and start/stop/wait and denies everything else (verified:
`POST /containers/create` -> 403). It lives on an internal network reachable only by
Sablier. One rule was added to the upstream template (Sablier calls `/wait` after a stop).

**Public access.** Tailscale Funnel exposes only the gateway's public port (ADR-0004).
The web container's protections (route allow-list, rate limits, body limit, CSP) apply.

**Delivery.** CD publishes amd64 images to GHCR only for commits on `main` whose CI passed,
with SBOM and signed build provenance; the server runs pinned commit SHAs, never builds.

## Consequences

- Idle footprint: gateway (~100 MB) + object storage; all compute containers at zero.
- The first visitor after a quiet period waits ~3-5 s; the "waking up" page explains it.
- A newly promoted champion reaches users on the first refresh after the API wakes.
- Hardening lessons: Caddy's file capability (`cap_net_bind_service`) cannot be exec'd
  under `cap_drop: ALL` + `no-new-privileges` (removed, we listen on 8090); Go
  dependencies in the compiled Caddy are pinned with `xcaddy --replace` for security fixes.
- Airflow is not a 24/7 scheduler any more: missed months run at next boot
  (`Persistent=true`), and backfills are a manual `systemctl start` with a month param.
