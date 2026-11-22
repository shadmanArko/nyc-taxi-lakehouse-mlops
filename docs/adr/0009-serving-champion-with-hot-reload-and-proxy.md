# ADR-0009: Serving the registry champion with hot reload, behind a hardened proxy

- **Status:** Accepted
- **Date:** 2026-11-22

## Context

The API must serve whatever model the promotion gate (ADR-0008) made `@champion`, keep
serving through registry hiccups, and pick up a new champion without a redeploy. Phase 6
exposes the service publicly through an unauthenticated tunnel (ADR-0004), so the public
entry point needs abuse protection, and end users need something friendlier than JSON.

## Decision

**API serves an alias, pinned to a version.** `TAXI_API_MODEL_URI=models:/taxi-trip-duration@champion`.
A `ModelManager` resolves the alias to an exact version (one registry call) and loads
`models:/<name>/<version>`, so an alias moving mid-download cannot mix versions. Every
`TAXI_API_MODEL_REFRESH_SECONDS` (default 60) it re-resolves; only a *changed* version is
downloaded, and it goes live only after it loaded successfully. A failed refresh keeps the
old model; if the registry is down at startup the API still starts, `/health` is 200,
`/ready` is 503 and it keeps retrying. Every prediction returns the model version.

**The served model is the training pipeline.** The API depends on `taxi-features` and the
same pinned scikit-learn/LightGBM/skops versions (one `uv.lock`), converts the request
with `to_model_input`, and MLflow enforces the logged input signature. There is no
second implementation of feature engineering anywhere.

**Separate web container = UI + reverse proxy.** nginx (non-root, read-only) serves a
static page (vanilla JS, no third-party assets, strict CSP) and proxies an allow-list of
API routes: `POST /api/predict`, `GET /api/{zones,ready,health}`. Everything else under
`/api/` (including the interactive docs) is 404. Limits: 5 predictions/s per client with
burst 10 (then 429), 20 req/s otherwise, 2 KB request bodies (then 413), short upstream
timeouts. This container is what the Phase 6 tunnel will point at.

**Zones by name.** The API ships the official TLC zone lookup and serves it at `/zones`
(without 264 "Unknown" / 265 "Outside of NYC"), so the UI offers "JFK Airport", not 132.

## Consequences

- Verified locally: the API served champion v2 and answered `sample-request.json` with
  21.0 min; the UI estimated the same Midtown → Upper East Side trip at 16 min on a Sunday
  afternoon vs 21 min at Wednesday rush hour; the proxy returned 403 for a wrong method,
  404 for non-allow-listed routes, 413 for a 6 KB body and 429 after the burst.
- A promotion in MLflow reaches users within one refresh interval, with no deploy and no
  downtime; rollback is moving the alias back to `@previous`.
- The API image grows to ~200 MB (scikit-learn, LightGBM, pandas); acceptable for the
  guarantee of identical training and serving code.
- Reaching the server's MLflow from a container through the laptop tunnel requires its
  `Host` header (`host.docker.internal:15050`) in MLflow's `--allowed-hosts`; on the
  server the API will use `http://mlflow:5000` directly.
