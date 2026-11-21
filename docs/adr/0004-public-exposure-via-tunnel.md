# ADR-0004: Public exposure through an outbound tunnel, no open ports

- **Status:** Proposed (applied in Phase 6, after the server operator approves the change)
- **Date:** 2026-11-21

## Context

The web UI and API need a shareable public HTTPS link. There is no custom domain. The server
must not open inbound ports or bind services to anything but loopback.

## Options

| Option | Cost | Stable URL without a domain | Notes |
|---|---|---|---|
| Tailscale Funnel | Free | Yes (`<machine>.<tailnet>.ts.net`) | Server already on Tailscale; TLS terminated by Tailscale; limited to ports 443/8443/10000 |
| Cloudflare Quick Tunnel | Free | No (random `trycloudflare.com` URL per restart) | Fine for demos, bad for shared links |
| Cloudflare named tunnel | Free | Needs a domain on Cloudflare | Best option once a domain exists |
| Inbound port forwarding | Free | Needs dynamic DNS | Rejected: exposes the server's network directly |

## Decision

Use **Tailscale Funnel** for a stable public HTTPS URL. A reverse proxy on the server
(loopback only) routes paths to the UI and API. Admin tools (MLflow, Airflow) are **not**
exposed publicly; they stay reachable over the private tailnet only.

## Consequences

- No inbound ports, no domain needed, TLS handled for us.
- If a domain is bought later, switch to a Cloudflare named tunnel; only the tunnel config
  changes, no service changes.
- Funnel makes the endpoints public and **unauthenticated**: anyone with the link can call
  them. The UI and `/predict` are meant to be public; they get rate limiting and request
  size limits at the proxy (Phase 6), and nothing private is reachable through the tunnel.
