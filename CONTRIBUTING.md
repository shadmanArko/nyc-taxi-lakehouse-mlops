# Contributing

## Setup

```bash
make setup   # uv sync --all-packages + install git hooks
```

## Workflow

1. Branch from `main`: `feat/...`, `fix/...`, `data/...`, `ml/...`, `infra/...`, `docs/...`.
2. Commit with [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat(api): add /ready endpoint`). The `commit-msg` hook enforces it.
3. Hooks run automatically:
   - **pre-commit:** Ruff format + lint, gitleaks, hadolint, actionlint, YAML/TOML/JSON checks,
     large-file guard, `uv.lock` sync.
   - **pre-push:** `mypy --strict` and the full test suite.
4. Open a PR and fill in the template. CI must be green; the single required status is
   `ci-ok`, which passes only when every job passes: hooks + full-history secret scan,
   `mypy --strict`, tests (coverage ≥ 85%), `linux/amd64` image build with a container smoke
   test and vulnerability scan, and (on PRs) dependency review.

## Rules

- **Never commit** data, model artifacts, `.env` files or secrets. Data goes through DVC.
- **Contracts:** changing `libs/contracts` means bumping `CONTRACT_VERSION` and the package
  version (semver), regenerating the schema snapshot
  (`UPDATE_CONTRACT_SNAPSHOT=1 uv run pytest libs/contracts`) and updating every consumer in
  the same PR. The snapshot test fails on any unannounced schema change.
- **Decisions:** a design change comes with an ADR in `docs/adr/`.
- **Server changes** are written as deployment requests (see [docs/deployment.md](docs/deployment.md)),
  never applied by hand.

## Useful commands

```bash
make help       # list everything
make check      # what CI runs (minus the image build)
make image      # build the API image for linux/amd64
make up / down  # local stack
```
