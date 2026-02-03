# Crawl preflight audit playbook (production VPS)

Goal: catch obvious blockers before a large crawl (especially the Jan 01 UTC annual campaign).

Canonical references:

- Production runbook: `../../../deployment/production-single-vps.md`
- Annual scope/seeds (source of truth): `../../annual-campaign.md`
- Growth/storage policy: `../../growth-constraints.md`
- Baseline drift (policy vs reality): `../../baseline-drift.md`
- Automation posture: `../../automation-implementation-plan.md`, `../../../deployment/systemd/README.md`

## Preconditions

- You are on the VPS.
- Backend repo is present (default): `/opt/healtharchive-backend`
- Venv exists at: `/opt/healtharchive-backend/.venv`
- Backend env file exists: `/etc/healtharchive/backend.env`

## If you need to temporarily defer the annual crawl

If you aren’t ready to run the annual campaign on Jan 01 UTC, disable the
systemd timer **and** remove the automation sentinel:

```bash
sudo systemctl disable --now healtharchive-schedule-annual.timer
sudo rm -f /etc/healtharchive/automation-enabled
```

Verify:

```bash
systemctl is-enabled healtharchive-schedule-annual.timer
systemctl status healtharchive-schedule-annual.timer
ls -la /etc/healtharchive/automation-enabled
```

Notes:

- This prevents **automatic** annual job enqueueing; it does not cancel any
  already-queued jobs.
- The safe validation unit `healtharchive-schedule-annual-dry-run.service` can
  still be run manually.

## Procedure (recommended)

1. Choose the annual campaign year:
   - If it’s before Jan 01 (UTC), use the upcoming year (e.g., Dec 2025 → `2026`).
2. (Recommended) Run a rehearsal with caps (generates active-load evidence):
   - `cd /opt/healtharchive-backend`
   - `./scripts/vps-smoke-crawl-rehearsal.sh --apply --source cihr --page-limit 25 --depth 1`
3. Run the preflight audit:
   - `cd /opt/healtharchive-backend`
   - `YEAR=2026; ./scripts/vps-preflight-crawl.sh --year "$YEAR"`

This writes a timestamped report directory under `/srv/healtharchive/ops/preflight/`.

## If it fails (common fixes)

- **Campaign storage forecast fails** (even if you’re below 80% *today*): the annual campaign is projected to exceed disk headroom or the 80% review threshold. Follow the report output to free space or expand disk *before* Jan 01 UTC.
- **Campaign storage forecast fails but you are using tiered storage (Storage Box)**: run preflight with the campaign tier root so the forecast uses the correct filesystem, e.g. `YEAR=2026; ./scripts/vps-preflight-crawl.sh --year "$YEAR" --campaign-archive-root /srv/healtharchive/storagebox/jobs`.
- **Rehearsal evidence (active crawl headroom) fails**: you don’t have a recent `--apply` rehearsal (or it recorded low MemAvailable / high swap). Run `./scripts/vps-smoke-crawl-rehearsal.sh --apply ...` to generate evidence, or upgrade the VPS / reduce crawl concurrency.
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
- **Ops automation posture fails**: enable required timers and sentinels (at minimum baseline drift), e.g. `sudo systemctl enable --now healtharchive-baseline-drift-check.timer && sudo touch /etc/healtharchive/baseline-drift-enabled`.
- **Admin/metrics auth check fails**: ensure a real `HEALTHARCHIVE_ADMIN_TOKEN` is set in production and routing is correct.

## Optional deep checks

- Run a small crawl rehearsal (capped crawl + indexing, isolated sandbox DB). This is the best way to validate headroom under active crawl load, not just idle host metrics:
  - `cd /opt/healtharchive-backend`
  - Dry-run: `./scripts/vps-smoke-crawl-rehearsal.sh --source cihr`
  - Apply: `./scripts/vps-smoke-crawl-rehearsal.sh --apply --source cihr --page-limit 25 --depth 1`
  - Evidence artifacts: `.../98-resource-monitor.jsonl` and `.../98-resource-summary.json`

- Validate the systemd wrapper (safe dry-run):
  - `sudo systemctl start healtharchive-schedule-annual-dry-run.service`
  - `sudo journalctl -u healtharchive-schedule-annual-dry-run.service -n 200 --no-pager`
- Capture a redacted “baseline inventory” snapshot:
  - `OUT_DIR="/srv/healtharchive/ops/preflight/$(date -u +%Y%m%dT%H%M%SZ)"; ./scripts/capture-baseline-inventory.sh --env-file /etc/healtharchive/backend.env --out "$OUT_DIR/baseline-inventory.txt"`

## Optional cleanup (disk hygiene)

If you ran multiple rehearsals or preflight runs, keep only the most recent few
directories as evidence and reclaim space.

Keep the latest 3 rehearsal runs (removes older ones):

```bash
ls -1dt /srv/healtharchive/ops/rehearsal/* | tail -n +4 | sudo xargs -r rm -rf --
```

Keep the latest 10 preflight reports (removes older ones):

```bash
ls -1dt /srv/healtharchive/ops/preflight/* | tail -n +11 | sudo xargs -r rm -rf --
```

## What “done” means

- `YEAR=2026; ./scripts/vps-preflight-crawl.sh --year "$YEAR"` exits `0`.
- The report directory exists and is retained as operator evidence for that crawl run.

## During the crawl (ongoing monitoring)

Once a large crawl is running, use the read-only status snapshot script for a
quick “all the basics” check:

```bash
cd /opt/healtharchive-backend
./scripts/vps-crawl-status.sh --year 2026
```
