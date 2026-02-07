# 2026-02-07: Deploy Workflow Hardening (Single VPS)

**Plan Version**: v1.1
**Status**: Implemented in Repo (operator adoption required on VPS)
**Scope**: Make deploys boring, repeatable, and crawl-safe on the single Hetzner VPS.

## Current State Summary

- Canonical deploy entrypoint is `scripts/vps-deploy.sh` (dry-run by default, `--apply` to deploy).
- The deploy script:
  - refuses dirty working trees unless `--allow-dirty`,
  - restarts API by default,
  - skips worker restart when there are running jobs (crawl-safe),
  - runs baseline drift checks and public surface verification by default.

Recent operator friction observed:

1. A shell alias that included `set -euo pipefail` could terminate an interactive shell on deploy failure, closing SSH sessions.
2. Public surface verification can fail due to external frontend hosting issues (e.g., Vercel `402 Payment required`), even when backend deploy is healthy.

## Goals

- Provide a single command for routine deploys that:
  - is safe in interactive shells,
  - is strict about git cleanliness,
  - preserves crawl-safety defaults,
  - supports a clearly-labeled backend-only mode when the public frontend is externally broken.

## Non-Goals

- Automatically skipping public-surface verification without operator intent.
- Changing baseline drift checks or core deploy semantics in `vps-deploy.sh`.

## Phase 1: Add `vps-hetzdeploy.sh` Wrapper (Implemented)

**Deliverables**:

- Wrapper script:
  - `scripts/vps-hetzdeploy.sh`
  - Default: `--mode full` (includes public verify).
  - Optional: `--mode backend-only` (adds `--skip-public-surface-verify`).
  - Always: `git fetch --prune`, `git checkout main`, `git pull --ff-only`.
  - Refuses dirty trees before deploy.

**Validation**:

- Script is `bash -n` clean.
- Local CI remains green.

## Phase 2: Operator Adoption (Pending; VPS)

**Steps (VPS)**:

```bash
cd /opt/healtharchive-backend
git pull --ff-only

# Recommended: install the wrapper as a real command (avoid fragile shell aliases)
sudo ./scripts/vps-install-hetzdeploy.sh --apply

# If you previously defined an alias named "hetzdeploy", remove it so flags like --mode work:
unalias hetzdeploy 2>/dev/null || true

# Routine deploy gate (strict; includes public verify)
./scripts/vps-hetzdeploy.sh

# Backend-only (use only while frontend is externally down)
./scripts/vps-hetzdeploy.sh --mode backend-only

# Or, if installed to /usr/local/bin:
hetzdeploy
hetzdeploy --mode backend-only
```

**Rollback**:

- Continue using `./scripts/vps-deploy.sh --apply ...` directly.

## Phase 3: Shell Hygiene Guardrails (Implemented)

**Goal**: prevent deploy friction caused by fragile aliases and persistent shell options.

**Deliverables**:

- Install helper:
  - `scripts/vps-install-hetzdeploy.sh`
- Updated playbooks to explicitly recommend `hetzdeploy` as a command (not an alias) and to remove legacy aliases.

**Validation**:

- `make ci` passes.
- On the VPS, `type hetzdeploy` should report a file under `/usr/local/bin/` (not an alias).
