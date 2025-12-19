# Systemd unit templates (single VPS)

These files are **templates** meant to be copied onto the production VPS under
`/etc/systemd/system/`.

They implement:

- Phase 5: annual scheduling timer (Jan 01 UTC)
- Phase 6: worker priority lowering during campaign (always-on, low-risk)
- Phase 8: replay reconciliation timer (pywb indexing; capped)
- Phase 4: optional "timer ran" pings (Healthchecks-style)
- Phase 5 (ops): annual search verification capture (optional, safe)

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
- `scripts/systemd-healthchecks-wrapper.sh`
  - Helper for optional Healthchecks-style pings without embedding ping URLs in unit files.
- `healtharchive-annual-search-verify.service`
  - Runs `scripts/annual-search-verify.sh` daily, but captures **once per year** (idempotent).
- `healtharchive-annual-search-verify.timer`
  - Daily timer for `healtharchive-annual-search-verify.service`.

---

## Install / update on the VPS

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
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-annual-search-verify.service \
  /etc/systemd/system/healtharchive-annual-search-verify.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-annual-search-verify.timer \
  /etc/systemd/system/healtharchive-annual-search-verify.timer
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
