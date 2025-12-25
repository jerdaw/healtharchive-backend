# Crawl preflight audit playbook (production VPS)

Goal: catch obvious blockers before a large crawl (especially the Jan 01 UTC annual campaign).

Canonical references:

- Production runbook: `../../deployment/production-single-vps.md`
- Annual scope/seeds (source of truth): `../annual-campaign.md`
- Growth/storage policy: `../growth-constraints.md`
- Baseline drift (policy vs reality): `../baseline-drift.md`
- Automation posture: `../automation-implementation-plan.md`, `../../deployment/systemd/README.md`

## Preconditions

- You are on the VPS.
- Backend repo is present (default): `/opt/healtharchive-backend`
- Venv exists at: `/opt/healtharchive-backend/.venv`
- Backend env file exists: `/etc/healtharchive/backend.env`

## Procedure (recommended)

1. Choose the annual campaign year:
   - If it’s before Jan 01 (UTC), use the upcoming year (e.g., Dec 2025 → `2026`).
2. Run the preflight audit:
   - `cd /opt/healtharchive-backend`
   - `./scripts/vps-preflight-crawl.sh --year <YYYY>`

This writes a timestamped report under:

- `/srv/healtharchive/ops/preflight/<timestamp>/`

## If it fails (common fixes)

- **Campaign storage forecast fails** (even if you’re below 80% *today*): the annual campaign is projected to exceed disk headroom or the 80% review threshold. Follow the report output to free space or expand disk *before* Jan 01 UTC.
- **CPU/RAM headroom fails**: the VPS is already under sustained load / memory pressure (or swap usage). Stop other heavy work (indexing, other crawls), then re-run preflight; if it persists, reduce crawl concurrency or upgrade the VPS.
- **Time sync (NTP) fails**: fix time sync before crawling (TLS, scheduling, and log correlation all assume correct UTC).
- **Docker daemon access fails**: Docker is installed but not usable by the current user (or the daemon is down). Fix `systemctl status docker`, user group membership, and re-run.
- **DB connectivity / Alembic-at-head fails**: DB is down or the schema is behind the code version; apply migrations (`alembic upgrade head`) and re-run.
- **Seed reachability fails**: the annual seed URLs aren’t reachable from the VPS right now; fix DNS/network/firewall issues (or investigate upstream downtime) before Jan 01 UTC.
- **Disk usage is high (>= 80%)**: pause, free space or expand disk before crawling.
- **Backups are missing/stale**: fix backups before crawling (don’t run long jobs without recoverability).
- **`/api/health` fails on loopback**: fix API/DB/service health first (check `systemctl status` + `journalctl`).
- **Annual scheduler dry-run errors**:
  - Missing `Source` rows → run `ha-backend seed-sources`.
  - Duplicated annual jobs for the year → resolve duplicates before scheduling.
  - Active jobs blocking scheduling → finish/index them (or decide not to run annual yet).
- **Temp cleanup candidates**: the report lists indexed jobs that still have `.tmp*` dirs; use `ha-backend cleanup-job --mode temp-nonwarc` (safe) to reclaim space.
- **Baseline drift failures**: reconcile production with `docs/operations/production-baseline-policy.toml`, then re-run drift checks.
- **Admin/metrics auth check fails**: ensure a real `HEALTHARCHIVE_ADMIN_TOKEN` is set in production and routing is correct.

## Optional deep checks

- Run a small crawl rehearsal (capped crawl + indexing, isolated sandbox DB):
  - `cd /opt/healtharchive-backend`
  - Dry-run: `./scripts/vps-smoke-crawl-rehearsal.sh --source cihr`
  - Apply: `./scripts/vps-smoke-crawl-rehearsal.sh --apply --source cihr --page-limit 25 --depth 1`

- Validate the systemd wrapper (safe dry-run):
  - `sudo systemctl start healtharchive-schedule-annual-dry-run.service`
  - `sudo journalctl -u healtharchive-schedule-annual-dry-run.service -n 200 --no-pager`
- Capture a redacted “baseline inventory” snapshot:
  - `./scripts/capture-baseline-inventory.sh --env-file /etc/healtharchive/backend.env --out /srv/healtharchive/ops/preflight/<timestamp>/baseline-inventory.txt`

## What “done” means

- `./scripts/vps-preflight-crawl.sh --year <YYYY>` exits `0`.
- The report directory exists and is retained as operator evidence for that crawl run.
