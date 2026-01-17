# Developer environment setup (local + VPS)

This document answers two questions:

1) How to set up a local dev environment for HealthArchive (backend + frontend).
2) Where to run which commands (your dev machine vs the production VPS).

For full backend live-testing flows, see `live-testing.md`.

---

## Repo layout

HealthArchive currently lives as **multiple repos**:

- Backend: https://github.com/jerdaw/healtharchive-backend (local dir: `healtharchive-backend/`)
- Frontend: https://github.com/jerdaw/healtharchive-frontend (local dir: `healtharchive-frontend/`)
- Datasets: https://github.com/jerdaw/healtharchive-datasets (local dir: `healtharchive-datasets/`)

Many developers keep them in one folder and use the mono-repo root `Makefile`
to run checks across repos.

---

## Local machine setup (recommended)

### 0) Prereqs

- `git`
- `python3` (match `healtharchive-backend` requirements)
- `node` (match frontend `engines.node`: https://github.com/jerdaw/healtharchive-frontend/blob/main/package.json)
- `make`

Recommended:

- `pipx` (for global Python tools like `pre-commit`)
- Docker (only needed for end-to-end crawling runs)

### 1) Backend setup (local)

From `healtharchive-backend/`:

```bash
make venv
make check
```

Then follow `docs/development/live-testing.md` for running the API locally,
running worker flows, and Docker-based crawling tests.

### 2) Frontend setup (local)

From `healtharchive-frontend/`:

```bash
npm ci
npm run check
```

### 3) Local guardrails (recommended for solo-fast direct-to-main)

If you’re moving fast and pushing directly to `main`, you want local guardrails
that reduce “oops I forgot to run checks” mistakes.

#### One-command check (workspace root)

From the mono-repo root:

```bash
make check
```

This runs:

- backend `make check`
- frontend `pre-commit run --all-files` + `npm run check`
- datasets checks

#### Pre-push hooks (recommended)

These run automatically on `git push`:

- Backend (runs `make check`; set `HA_PRE_PUSH_FULL=1` to run `make check-full`):
  - `scripts/install-pre-push-hook.sh`
- Frontend (runs `pre-commit` + `npm run check`):
  - https://github.com/jerdaw/healtharchive-frontend/blob/main/scripts/install-pre-push-hook.sh

Install them on your dev machine:

```bash
cd healtharchive-backend && ./scripts/install-pre-push-hook.sh
cd ../healtharchive-frontend && ./scripts/install-pre-push-hook.sh
```

Bypass once if needed (emergency only):

- `git push --no-verify`
- or set `HA_SKIP_PRE_PUSH=1`

#### Pre-commit (recommended)

Both repos include `.pre-commit-config.yaml`.

- Install once: `pipx install pre-commit`
- Enable “run on commit” inside each repo:
  - `pre-commit install`

---

## VPS usage (production)

### What runs on the VPS

Run these on the production VPS (typically from `/opt/healtharchive-backend`):

- Deploy + restart services:
  - `./scripts/vps-deploy.sh --apply`
- Production verification gates:
  - `./scripts/check_baseline_drift.py --mode live`
  - `./scripts/verify_public_surface.py`
- Ops bootstrap / automation helpers (recommended):
  - one-time: `sudo ./scripts/vps-bootstrap-ops-dirs.sh`
  - install/update systemd templates: `sudo ./scripts/vps-install-systemd-units.sh --apply --restart-worker`
  - verify timers/sentinels: `./scripts/verify_ops_automation.sh`

Recommended deploy flow (single command):

```bash
./scripts/vps-deploy.sh --apply --baseline-mode live
```

Note: systemd timer enablement is explicit and gated by sentinel files under `/etc/healtharchive/`.
For enable/rollback steps, see `../deployment/systemd/README.md`.

### What should *not* run on the VPS

These are local-developer guardrails and should run on your dev machine:

- `make check` (workspace root)
- `healtharchive-backend/scripts/install-pre-push-hook.sh` (this repo)
- Frontend hook installer: https://github.com/jerdaw/healtharchive-frontend/blob/main/scripts/install-pre-push-hook.sh

Reason: hooks install into `.git/hooks/` for the repo you’re pushing from (your
laptop/workstation), not on the server.
