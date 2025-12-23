# Contributing (HealthArchive Backend)

## Quickstart

- Create venv + install dev deps: `make venv`
- Full checks (what CI runs): `make check`

## Optional: pre-commit

This repo includes a `.pre-commit-config.yaml` with fast, mechanical checks (whitespace/EOF, YAML/TOML validation, detecting private keys).

- Included in `make check` (recommended for catching CI failures locally).
- Enable “run on commit” (recommended):
  - `pre-commit install`
