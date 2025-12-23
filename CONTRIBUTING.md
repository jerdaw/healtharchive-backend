# Contributing (HealthArchive Backend)

## Quickstart

- Create venv + install dev deps: `make venv`
- Full checks (what CI runs): `make check`

## Optional: pre-commit

This repo includes a `.pre-commit-config.yaml` with fast, mechanical checks (whitespace/EOF, YAML/TOML validation, detecting private keys).

- Install (recommended): `python -m pip install --user pre-commit`
- Enable: `pre-commit install`
- Run on demand: `pre-commit run --all-files`
