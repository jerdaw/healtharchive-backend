# Systemd unit templates (single VPS)

These files are **templates** meant to be copied onto the production VPS under
`/etc/systemd/system/`.

They implement:

- Annual scheduling timer (Jan 01 UTC)
- Worker priority lowering during campaign (always-on, low-risk)
- Storage Box mount (sshfs) for cold WARC storage (optional but recommended for tiering)
- WARC tiering bind mounts (Storage Box -> canonical paths) (optional; for tiny-SSD setups)
- Replay reconciliation timer (pywb indexing; capped)
- Change tracking timer (edition-aware diffs; capped)
- Baseline drift check timer (policy vs observed; detects config drift)
- Public surface verification timer (public API + frontend; deeper than uptime checks)
- Optional "timer ran" pings (Healthchecks-style)
- Annual search verification capture (optional, safe)

Assumptions (adjust paths/user if your VPS differs):

- Repo is deployed at: `/opt/healtharchive-backend`
- Venv exists at: `/opt/healtharchive-backend/.venv`
- Backend env file: `/etc/healtharchive/backend.env`
- Backend system user: `haadmin`

---

## Files

- `healtharchive-schedule-annual.service`
  - **Apply mode**: enqueues annual jobs (`--apply`) for the current UTC year.
  - Gated by `ConditionPathExists=/etc/healtharchive/automation-enabled`.
  - `RefuseManualStart=yes` to prevent accidental `systemctl start` while the
    worker is running.
- `healtharchive-schedule-annual.timer`
  - Runs at `*-01-01 00:05:00 UTC`
  - `Persistent=true` (runs on next boot if missed)
- `healtharchive-schedule-annual-dry-run.service`
  - Safe validation service (no DB writes).
- `healtharchive-worker.service.override.conf`
  - Drop-in that lowers worker CPU/IO priority to keep the API responsive.
- `healtharchive-replay-reconcile.service`
  - **Apply mode**: runs `ha-backend replay-reconcile --apply --max-jobs 1`.
  - Gated by `ConditionPathExists=/etc/healtharchive/replay-automation-enabled`.
  - Uses a lock file under `/srv/healtharchive/replay/.locks/` to prevent concurrent runs.
- `healtharchive-replay-reconcile.timer`
  - Daily at `*-*-* 02:30:00 UTC`
  - `Persistent=true` (runs on next boot if missed)
- `healtharchive-replay-reconcile-dry-run.service`
  - Safe validation service (no docker exec, no filesystem writes beyond the lock file dir).
- `healtharchive-change-tracking.service`
  - **Apply mode**: runs `ha-backend compute-changes` (edition-aware diffs).
  - Gated by `ConditionPathExists=/etc/healtharchive/change-tracking-enabled`.
- `healtharchive-change-tracking.timer`
  - Daily at `*-*-* 03:40:00 UTC`
  - `Persistent=true` (runs on next boot if missed)
- `healtharchive-change-tracking-dry-run.service`
  - Safe validation service (no DB writes; reports how many diffs would be computed).
- `scripts/systemd-healthchecks-wrapper.sh`
  - Helper for optional Healthchecks-style pings without embedding ping URLs in unit files.
- `healtharchive-annual-search-verify.service`
  - Runs `scripts/annual-search-verify.sh` daily, but captures **once per year** (idempotent).
- `healtharchive-annual-search-verify.timer`
  - Daily timer for `healtharchive-annual-search-verify.service`.
- `healtharchive-baseline-drift-check.service`
  - Runs `scripts/check_baseline_drift.py` (policy vs observed; writes artifacts under `/srv/healtharchive/ops/baseline/`).
  - Gated by `ConditionPathExists=/etc/healtharchive/baseline-drift-enabled`.
- `healtharchive-baseline-drift-check.timer`
  - Weekly timer for `healtharchive-baseline-drift-check.service`.
- `healtharchive-public-surface-verify.service`
  - Runs `scripts/verify_public_surface.py` (public API + frontend; includes changes/RSS and partner pages).
  - Gated by `ConditionPathExists=/etc/healtharchive/public-verify-enabled`.
  - Intended as a deeper “synthetic check” than external uptime monitors.
- `healtharchive-public-surface-verify.timer`
  - Daily timer for `healtharchive-public-surface-verify.service`.
- `healtharchive-tiering-metrics.service` + `.timer`
  - Writes a small set of tiering health metrics to the node_exporter textfile collector.
  - Used to alert on Storage Box / tiering failures without needing a systemd collector.
  - Prereq: node_exporter must run with `--collector.textfile.directory=/var/lib/node_exporter/textfile_collector`
    (configured by `scripts/vps-install-observability-exporters.sh`).
- `healtharchive-storagebox-sshfs.service`
  - Mounts a Hetzner Storage Box at `/srv/healtharchive/storagebox` via `sshfs`.
  - Reads configuration from `/etc/healtharchive/storagebox.env`.
  - Intended for tiered WARC storage on small SSD hosts.
- `healtharchive-warc-tiering.service`
  - Applies bind mounts from `/etc/healtharchive/warc-tiering.binds` so canonical
    archive paths under `/srv/healtharchive/jobs/**` resolve to Storage Box data.
  - Runs before the API/worker/replay services start.
- `healtharchive-annual-output-tiering.service`
  - After annual jobs are enqueued, bind-mounts each annual job output_dir onto the Storage Box tier.
  - Triggered via `OnSuccess=` in `healtharchive-schedule-annual.service` (template).
- `healtharchive-annual-campaign-sentinel.service` + `.timer`
  - Runs a “day-of” annual readiness gate automatically: preflight + annual-status + tiering checks.
  - Writes a small Prometheus textfile metric so Alertmanager can notify on failures.

---

## Recommended enablement guidance

These timers are safe-by-default and gated by sentinel files. Enable only what
matches your operational readiness.

- **Change tracking** (`healtharchive-change-tracking.timer`)
  - Recommended to enable once the `snapshot_changes` table exists and a dry
    run succeeds without errors.
- **Annual scheduling** (`healtharchive-schedule-annual.timer`)
  - Enable only after an annual dry-run succeeds and storage headroom is
    confirmed.
- **Replay reconcile** (`healtharchive-replay-reconcile.timer`)
  - Enable only if replay is enabled and stable.
- **Annual search verification** (`healtharchive-annual-search-verify.timer`)
  - Optional; safe to enable if you want a yearly search QA artifact.
- **Baseline drift check** (`healtharchive-baseline-drift-check.timer`)
  - Recommended; low-risk and catches “silent” ops drift.

If a timer is enabled, also ensure its sentinel file exists under
`/etc/healtharchive/` (see the enablement sections below).

---

## Install / update on the VPS

Preferred (one command; installs templates + worker priority drop-in):

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-install-systemd-units.sh --apply --restart-worker
```

If you are using WARC tiering with a Storage Box, also create these files on the VPS:

- `/etc/healtharchive/storagebox.env`
  - Configuration consumed by `healtharchive-storagebox-sshfs.service`.
- `/etc/healtharchive/warc-tiering.binds`
  - Bind mount manifest consumed by `healtharchive-warc-tiering.service`.

See: `docs/operations/playbooks/warc-storage-tiering.md`.

Before enabling timers that write artifacts under `/srv/healtharchive/ops/`, ensure
the ops directories exist with the expected permissions:

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-bootstrap-ops-dirs.sh
```

Manual install (equivalent):

Copy unit files:

```bash
sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-schedule-annual.service \
  /etc/systemd/system/healtharchive-schedule-annual.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-schedule-annual.timer \
  /etc/systemd/system/healtharchive-schedule-annual.timer

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-schedule-annual-dry-run.service \
  /etc/systemd/system/healtharchive-schedule-annual-dry-run.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-replay-reconcile.service \
  /etc/systemd/system/healtharchive-replay-reconcile.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-replay-reconcile.timer \
  /etc/systemd/system/healtharchive-replay-reconcile.timer

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-replay-reconcile-dry-run.service \
  /etc/systemd/system/healtharchive-replay-reconcile-dry-run.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-change-tracking.service \
  /etc/systemd/system/healtharchive-change-tracking.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-change-tracking.timer \
  /etc/systemd/system/healtharchive-change-tracking.timer

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-change-tracking-dry-run.service \
  /etc/systemd/system/healtharchive-change-tracking-dry-run.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-annual-search-verify.service \
  /etc/systemd/system/healtharchive-annual-search-verify.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-annual-search-verify.timer \
  /etc/systemd/system/healtharchive-annual-search-verify.timer

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-baseline-drift-check.service \
  /etc/systemd/system/healtharchive-baseline-drift-check.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-baseline-drift-check.timer \
  /etc/systemd/system/healtharchive-baseline-drift-check.timer

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-public-surface-verify.service \
  /etc/systemd/system/healtharchive-public-surface-verify.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-public-surface-verify.timer \
  /etc/systemd/system/healtharchive-public-surface-verify.timer
```

Install the worker priority drop-in:

```bash
sudo install -d -m 0755 -o root -g root /etc/systemd/system/healtharchive-worker.service.d
sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-worker.service.override.conf \
  /etc/systemd/system/healtharchive-worker.service.d/override.conf
```

Reload systemd:

```bash
sudo systemctl daemon-reload
```

Restart worker to pick up priority changes:

```bash
sudo systemctl restart healtharchive-worker
```

Verify the priority values:

```bash
systemctl show healtharchive-worker -p Nice -p IOSchedulingClass -p IOSchedulingPriority
```

---

## Optional: "timer ran" pings (Healthchecks-style)

This repo does not commit ping URLs. If you want "did it run?" checks, create a
root-owned env file on the VPS:

```bash
sudo install -d -m 0755 -o root -g root /etc/healtharchive
sudo install -m 0600 -o root -g root /dev/null /etc/healtharchive/healthchecks.env
```

Edit `/etc/healtharchive/healthchecks.env` and set (examples):

```bash
HEALTHARCHIVE_HC_PING_REPLAY_RECONCILE=https://hc-ping.com/<uuid>
HEALTHARCHIVE_HC_PING_SCHEDULE_ANNUAL=https://hc-ping.com/<uuid>
HEALTHARCHIVE_HC_PING_ANNUAL_SENTINEL=https://hc-ping.com/<uuid>
HEALTHARCHIVE_HC_PING_CHANGE_TRACKING=https://hc-ping.com/<uuid>
HEALTHARCHIVE_HC_PING_BASELINE_DRIFT=https://hc-ping.com/<uuid>
HEALTHARCHIVE_HC_PING_PUBLIC_VERIFY=https://hc-ping.com/<uuid>
```

Notes:

- The unit templates use `EnvironmentFile=-/etc/healtharchive/healthchecks.env`
  so the file is optional.
- If set, services will best-effort ping:
  - `<url>/start` at the beginning
  - `<url>` on success
  - `<url>/fail` on failure
- Ping failures do not fail the service.

---

## Validate the annual scheduler (safe)

This dry-run service exercises DB connectivity + scheduler output without
creating jobs:

```bash
sudo systemctl start healtharchive-schedule-annual-dry-run.service
sudo journalctl -u healtharchive-schedule-annual-dry-run.service -n 200 --no-pager
```

Do **not** run `healtharchive-schedule-annual.service` manually in production;
it enqueues jobs and the worker may start crawling immediately.

---

## Validate replay reconciliation (safe)

This dry-run service exercises DB connectivity + filesystem drift detection
without running any docker exec commands:

```bash
sudo systemctl start healtharchive-replay-reconcile-dry-run.service
sudo journalctl -u healtharchive-replay-reconcile-dry-run.service -n 200 --no-pager
```

---

## Validate change tracking (safe)

This dry-run service exercises DB connectivity and reports how many diffs would
be computed:

```bash
sudo systemctl start healtharchive-change-tracking-dry-run.service
sudo journalctl -u healtharchive-change-tracking-dry-run.service -n 200 --no-pager
```

If you see an error like `relation "snapshot_changes" does not exist`, apply
migrations first (idempotent):

```bash
cd /opt/healtharchive-backend
sudo -u haadmin /opt/healtharchive-backend/.venv/bin/alembic upgrade head
```

---

## Enable automation (Jan 01)

Create the automation sentinel file:

```bash
sudo install -m 0644 -o root -g root /dev/null /etc/healtharchive/automation-enabled
```

Enable the timer:

```bash
sudo systemctl enable --now healtharchive-schedule-annual.timer
systemctl list-timers | rg healtharchive-schedule-annual || systemctl list-timers | grep healtharchive-schedule-annual
```

Note: do not `enable` the `.service` units directly; only the `.timer` should be
enabled.

---

## Enable replay reconciliation automation (optional)

Create the replay automation sentinel file:

```bash
sudo install -m 0644 -o root -g root /dev/null /etc/healtharchive/replay-automation-enabled
```

Enable the timer:

```bash
sudo systemctl enable --now healtharchive-replay-reconcile.timer
systemctl list-timers | rg healtharchive-replay-reconcile || systemctl list-timers | grep healtharchive-replay-reconcile
```

Note: by default, the timer only reconciles **replay indexing**. Preview image
generation is intentionally left manual/capped until you decide it’s stable
enough to automate.

---

## Enable change tracking automation (optional)

Create the change tracking sentinel file:

```bash
sudo install -m 0644 -o root -g root /dev/null /etc/healtharchive/change-tracking-enabled
```

Enable the timer:

```bash
sudo systemctl enable --now healtharchive-change-tracking.timer
systemctl list-timers | rg healtharchive-change-tracking || systemctl list-timers | grep healtharchive-change-tracking
```

---

## Enable annual search verification capture (optional)

This captures golden-query `/api/search` JSON once per year **after** the annual
campaign becomes search-ready.

The service is idempotent:

- If the campaign isn't ready, it exits 0 (no failure spam).
- If artifacts already exist for the current year/run-id, it exits 0.

Enable the timer:

```bash
sudo systemctl enable --now healtharchive-annual-search-verify.timer
systemctl list-timers | rg healtharchive-annual-search-verify || systemctl list-timers | grep healtharchive-annual-search-verify
```

Artifacts default to:

- `/srv/healtharchive/ops/search-eval/<year>/final/`

To force a re-run for the current year, delete that directory and run the
service once.

---

## Enable baseline drift checks (recommended)

Baseline drift checks validate that production still matches the project’s
expected invariants (security posture, perms, unit enablement).

Create the sentinel file:

```bash
sudo install -m 0644 -o root -g root /dev/null /etc/healtharchive/baseline-drift-enabled
```

Enable the timer:

```bash
sudo systemctl enable --now healtharchive-baseline-drift-check.timer
systemctl list-timers | rg healtharchive-baseline-drift-check || systemctl list-timers | grep healtharchive-baseline-drift-check
```

Artifacts are written under:

- `/srv/healtharchive/ops/baseline/`

If the drift check fails, inspect:

- `/srv/healtharchive/ops/baseline/drift-report-latest.txt`
  - `journalctl -u healtharchive-baseline-drift-check.service --no-pager -l`

---

## Enable public surface verification (optional, recommended)

This is a deeper synthetic check than external uptime monitors. It validates:

- public API health, sources, search, snapshot detail and raw HTML
- replay browse URL (unless skipped)
- exports manifest and export endpoint HEADs
- changes feed + RSS
- key frontend pages, including `/brief`, `/cite`, `/methods`, and `/governance`

Create the sentinel file:

```bash
sudo install -m 0644 -o root -g root /dev/null /etc/healtharchive/public-verify-enabled
```

Enable the timer:

```bash
sudo systemctl enable --now healtharchive-public-surface-verify.timer
systemctl list-timers | rg healtharchive-public-surface-verify || systemctl list-timers | grep healtharchive-public-surface-verify
```

---

## Rollback / disable quickly

- Disable timer immediately:

  ```bash
  sudo systemctl disable --now healtharchive-schedule-annual.timer
  ```

- Disable all scheduling automation immediately:

  ```bash
  sudo rm -f /etc/healtharchive/automation-enabled
  ```

- Disable replay reconciliation automation immediately:

  ```bash
  sudo systemctl disable --now healtharchive-replay-reconcile.timer
  sudo rm -f /etc/healtharchive/replay-automation-enabled
  ```

- Disable annual search verification automation immediately:

  ```bash
  sudo systemctl disable --now healtharchive-annual-search-verify.timer
  ```

- Remove the worker priority override:

  ```bash
  sudo rm -f /etc/systemd/system/healtharchive-worker.service.d/override.conf
  sudo systemctl daemon-reload
  sudo systemctl restart healtharchive-worker
  ```
