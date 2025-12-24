# Contributing (HealthArchive Backend)

## Quickstart

- Create venv + install dev deps: `make venv`
- Full checks (what CI runs): `make check`

## Optional: pre-commit

This repo includes a `.pre-commit-config.yaml` with fast, mechanical checks (whitespace/EOF, YAML/TOML validation, detecting private keys).

- Included in `make check` (recommended for catching CI failures locally).
- Enable “run on commit” (recommended):
  - `pre-commit install`

## Optional: pre-push (recommended for solo-fast direct-to-main)

If you're pushing directly to `main`, a local pre-push hook helps keep "green main" true by running `make check` before every push.

- Install: `./scripts/install-pre-push-hook.sh`
- Bypass once: `git push --no-verify` (or set `HA_SKIP_PRE_PUSH=1`)
