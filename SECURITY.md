# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/shadmanArko/nyc-taxi-lakehouse-mlops/security/advisories/new),
not in public issues. You can expect an acknowledgement within a few days.

## Practices in this repository

- **Secrets:** never committed. gitleaks checks staged changes on every local commit, and CI
  scans the full git history on every push and pull request.
- **Supply chain:** dependencies locked (`uv.lock`), base images pinned by digest, GitHub
  Actions pinned by commit SHA, checkout without persisted credentials. Dependabot proposes
  updates weekly; pull requests get a dependency review.
- **Images:** every CI build of the API image is scanned (Grype) and fails on fixable
  high/critical vulnerabilities.
- **Runtime:** containers run as non-root with all Linux capabilities dropped and a read-only
  root filesystem; services bind to loopback only.
- **Public access (planned, [ADR-0004](docs/adr/0004-public-exposure-via-tunnel.md)):** an
  outbound tunnel with no inbound ports. Public endpoints are unauthenticated by design and
  protected by rate and request-size limits; admin tools (MLflow, Airflow) stay private.
