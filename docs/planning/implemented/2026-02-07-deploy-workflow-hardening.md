# Deploy Workflow Hardening (Implemented 2026-02-07)

**Status:** Implemented | **Scope:** Make routine deploys boring, repeatable, and crawl-safe on the single VPS.

## Outcomes

- Added a safe deploy wrapper with an explicit backend-only mode for external frontend outages:
  - `scripts/vps-hetzdeploy.sh` (`--mode full|backend-only`)
- Added an installer so operators use a real command instead of fragile shell aliases:
  - `scripts/vps-install-hetzdeploy.sh` (installs `hetzdeploy` under `/usr/local/bin/`)
- Preserved deploy safety properties:
  - refuses dirty trees by default
  - keeps worker restart crawl-safe (skips restart while jobs are running)
  - keeps baseline drift checks enabled by default

## Canonical Docs Updated

- `docs/operations/playbooks/core/deploy-and-verify.md`
- `docs/deployment/production-single-vps.md`
- `docs/deployment/production-rollout-checklist.md`

## Validation

- `make ci` remains green.
- On the VPS, `type hetzdeploy` reports `/usr/local/bin/hetzdeploy` (not an alias).

## Historical Context

Detailed iteration is preserved in git history and associated incident notes.
