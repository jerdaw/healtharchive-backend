# Healthchecks.io parity (env ↔ systemd ↔ Healthchecks)

**Do not enable or change production automations until the annual crawl/scrape is finished and the campaign jobs are indexed.**

Goal: ensure the Healthchecks.io dashboard is a faithful reflection of what the VPS actually runs (and only that).

This playbook focuses on three sources of truth:

1) **systemd timers** on the VPS (what actually runs)
2) `/etc/healtharchive/healthchecks.env` (which pings are wired on the VPS)
3) **Healthchecks.io checks** (what the dashboard expects to hear from)

Key rule:

- A Healthchecks.io check should exist **iff** there is a corresponding ping URL in `/etc/healtharchive/healthchecks.env` (or a legacy `HC_*` URL used by the disk/backup scripts).

If you follow that rule, the dashboard cannot drift into “checks we don’t use” or “missing checks for enabled jobs”.

---

## Current state (as of 2026-01-03)

These pings are configured in `/etc/healtharchive/healthchecks.env`:

- `HEALTHARCHIVE_HC_PING_REPLAY_RECONCILE` → `healtharchive-replay-reconcile.timer` (daily)
- `HEALTHARCHIVE_HC_PING_SCHEDULE_ANNUAL` → `healtharchive-schedule-annual.timer` (yearly)
- `HEALTHARCHIVE_HC_PING_ANNUAL_SENTINEL` → `healtharchive-annual-campaign-sentinel.timer` (yearly)
- `HEALTHARCHIVE_HC_PING_BASELINE_DRIFT` → `healtharchive-baseline-drift-check.timer` (weekly)
- `HEALTHARCHIVE_HC_PING_PUBLIC_VERIFY` → `healtharchive-public-surface-verify.timer` (daily)
- `HEALTHARCHIVE_HC_PING_ANNUAL_SEARCH_VERIFY` → `healtharchive-annual-search-verify.timer` (daily)
- `HEALTHARCHIVE_HC_PING_CHANGE_TRACKING` → `healtharchive-change-tracking.timer` (daily)
- `HEALTHARCHIVE_HC_PING_COVERAGE_GUARDRAILS` → `healtharchive-coverage-guardrails.timer` (daily)
- `HEALTHARCHIVE_HC_PING_REPLAY_SMOKE` → `healtharchive-replay-smoke.timer` (daily)

Legacy script checks (separate from the systemd wrapper):

- `HC_DB_BACKUP_URL` → `healtharchive-db-backup.timer` (daily)
- `HC_DISK_URL` + `HC_DISK_THRESHOLD` → `healtharchive-disk-check.timer` (hourly)

Known “not wired (by design) right now”:

- `HEALTHARCHIVE_HC_PING_CLEANUP_AUTOMATION` exists as a ping var in the installed unit, but:
  - `healtharchive-cleanup-automation.timer` is disabled, and
  - `/etc/healtharchive/cleanup-automation-enabled` sentinel is missing.
  - Result: do not create a Healthchecks.io check or env var until cleanup automation is intentionally enabled.

---

## Audit checklist (safe; no restarts)

### 1) List the ping vars currently configured (VPS env file)

This prints only variable names (not URLs):

```bash
sudo awk -F= '$1 ~ /^(HEALTHARCHIVE_HC_PING_|HC_)/ {print $1}' /etc/healtharchive/healthchecks.env | sort -u
```

### 2) List what timers are actually enabled (what will run)

```bash
systemctl list-timers --all | grep healtharchive-
```

### 3) Confirm Healthchecks.io check schedules match reality

Use the `NEXT` column from `systemctl list-timers` to configure Healthchecks schedules:

- Hourly timers (disk): Healthchecks “1 hour” period + ~2 hours grace.
- Daily timers: Healthchecks “1 day” period + ~6 hours grace.
- Yearly timers (schedule annual + annual sentinel): Healthchecks **cron in UTC** + **large grace** (7–14 days).

If a yearly check is configured with a small grace (hours), it will look “down” most of the year.

---

## Reconcile: achieve 1:1 parity (what exists vs what should exist)

### Rule A — If a timer is enabled and important, it should have a Healthchecks ping

For each enabled “important outcome” timer, ensure:

1) A Healthchecks.io check exists
2) Its ping URL is stored in `/etc/healtharchive/healthchecks.env`

Important outcome timers (recommended to monitor):

- `healtharchive-replay-reconcile.timer`
- `healtharchive-public-surface-verify.timer`
- `healtharchive-change-tracking.timer`
- `healtharchive-coverage-guardrails.timer`
- `healtharchive-replay-smoke.timer`
- `healtharchive-annual-search-verify.timer`
- `healtharchive-baseline-drift-check.timer`
- `healtharchive-schedule-annual.timer` (yearly)
- `healtharchive-annual-campaign-sentinel.timer` (yearly)
- legacy: `healtharchive-db-backup.timer`, `healtharchive-disk-check.timer`

High-frequency timers (recommended NOT to monitor in Healthchecks; too noisy):

- `healtharchive-crawl-metrics.timer`
- `healtharchive-tiering-metrics.timer`
- `healtharchive-crawl-auto-recover.timer`
- `healtharchive-storage-hotpath-auto-recover.timer`

### Rule B — If a Healthchecks.io check exists, it must correspond to a real job you run

If a Healthchecks.io check exists but:

- there is no enabled timer for it, and
- it is not one of the legacy script checks,

then delete it in Healthchecks.io and remove its env var from `/etc/healtharchive/healthchecks.env`.

---

## Cleanup automation: what remains to do (deferred until after crawl)

Cleanup automation is currently installed but intentionally disabled:

- Timer: `healtharchive-cleanup-automation.timer` (disabled)
- Sentinel: `/etc/healtharchive/cleanup-automation-enabled` (missing)
- Ping var supported by the unit: `HEALTHARCHIVE_HC_PING_CLEANUP_AUTOMATION`

### Why we are waiting

Enabling cleanup changes production behavior (even if intended to be safe). Defer until after crawl so:

- we avoid adding churn during the annual campaign,
- we can review retention expectations and confirm cleanup boundaries.

### Post-crawl enablement checklist (when you decide “yes, enable cleanup”)

1) Review the cleanup behavior and config:
   - Playbook: `../crawl/cleanup-automation.md`
   - Config: `/opt/healtharchive-backend/ops/automation/cleanup-automation.toml`

2) Decide Healthchecks schedule (from the timer):

```bash
systemctl cat healtharchive-cleanup-automation.timer
```

Current schedule (template): weekly Sunday 04:45 UTC.

Recommended Healthchecks schedule for that timer:

- Cron (UTC): `45 4 * * 0`
- Grace: 2 days

3) Create the Healthchecks.io check:
- Name: `healtharchive-cleanup-automation`
- Schedule: cron above (UTC)
- Grace: 2 days

4) Add the ping URL to `/etc/healtharchive/healthchecks.env`:

```bash
sudoedit /etc/healtharchive/healthchecks.env
```

Add:

```bash
HEALTHARCHIVE_HC_PING_CLEANUP_AUTOMATION=https://hc-ping.com/<uuid>
```

5) Enable cleanup automation (two gates):

```bash
sudo install -d -m 0755 /etc/healtharchive
sudo touch /etc/healtharchive/cleanup-automation-enabled
sudo systemctl enable --now healtharchive-cleanup-automation.timer
```

6) Verify the ping wiring (safe; does not run cleanup):

```bash
sudo bash -lc 'set -a; source /etc/healtharchive/healthchecks.env; set +a; /opt/healtharchive-backend/scripts/systemd-healthchecks-wrapper.sh --ping-var HEALTHARCHIVE_HC_PING_CLEANUP_AUTOMATION -- echo ok'
```

7) Verify real runs on the next scheduled window:

```bash
sudo journalctl -u healtharchive-cleanup-automation.service -n 200 --no-pager
```

If you decide “no, don’t enable cleanup”, keep it disabled and do not create the Healthchecks check or env var.
