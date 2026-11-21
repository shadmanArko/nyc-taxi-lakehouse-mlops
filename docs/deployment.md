# Deployment

Development happens on a laptop; everything is deployed to a single self-hosted Linux server
(`linux/amd64`) that is reachable only over a private VPN. Images are therefore always built
and tested for `linux/amd64` in CI.

## Container rules

**Applied now** in [`infra/compose/compose.yaml`](../infra/compose/compose.yaml) and the
API [Dockerfile](../services/api/Dockerfile):

- Base images pinned by exact tag **and** digest.
- Ports published on `127.0.0.1` only; never `0.0.0.0` or the LAN.
- Non-root user, `cap_drop: [ALL]`, `no-new-privileges`, read-only root filesystem with a
  small `tmpfs` for `/tmp`.
- CPU, memory and PID limits, health check, `restart: unless-stopped`.
- Log rotation (`max-size 10m`, `max-file 3`).
- Own Compose project and network; a service joins only the networks it needs.

**Applied on deployment** (Phase 6):

- Application images pulled from GHCR, pinned by tag and digest (built and scanned in CI).
- Secrets as files (mode 600) mounted read-only; never environment variables, never in Git.
- Durable data on the server's data disk under a per-project directory, included in backups.

## Change process

Changes to the server are never applied ad hoc:

1. Develop and test locally (synthetic data first).
2. Write a **deployment request**: compose file, config, secret *names*, data paths, loopback
   ports, CPU/RAM/GPU needs, backup needs, rollback steps.
3. The server operator reviews and approves the request; only then is it applied.
4. Verify on the server with synthetic data, then with the real workflow.

Deployment requests are kept in `infra/deploy-requests/` (Phase 6).

## Public access

See [ADR-0004](adr/0004-public-exposure-via-tunnel.md): an outbound tunnel, no open ports.
