# Phase 6 — Ops Cadence Checklist (internal)

Purpose: make routine operations repeatable and low-friction so the project can be maintained without heroics.

This checklist is intentionally short. If a task feels too heavy to do regularly, it should be moved to a longer cadence or automated safely.

## Weekly (10–15 minutes)

- **Service health**
  - `curl -sS http://127.0.0.1:8001/api/health; echo`
  - `sudo systemctl status healtharchive-api healtharchive-worker --no-pager -l`
- **Disk usage trend**
  - `df -h /`
  - If `/srv/healtharchive` exists: `du -sh /srv/healtharchive/* | sort -h | tail -n 5`
- **Recent errors**
  - `sudo journalctl -u healtharchive-api -n 200 --no-pager`
  - `sudo journalctl -u healtharchive-worker -n 200 --no-pager`
- **Change tracking timer** (if enabled)
  - `systemctl list-timers | rg healtharchive-change-tracking || systemctl list-timers | grep healtharchive-change-tracking`

## Monthly (30–60 minutes)

- **Reliability review** (can be folded into the impact report)
  - Note any incidents, slowdowns, or crawl failures.
  - Confirm `/status` and `/impact` look reasonable and are current.
- **Changelog update**
  - Add a short entry in `/changelog` reflecting meaningful updates.
- **Search quality spot-check** (lightweight)
  - Run a few common queries on `/archive` and ensure results look plausible.
- **Automation sanity check**
  - Verify timers are enabled only where intended.

## Quarterly (1–2 hours)

- **Restore test**
  - Follow `restore-test-procedure.md` and record results using `restore-test-log-template.md`.
- **Growth constraints review**
  - Revisit `growth-constraints.md` (storage, source caps, performance budgets).
  - Adjust only if you can still support the new limits.

## Annual (before Jan 01 UTC)

- **Annual edition readiness**
  - Review `annual-campaign.md` for scope changes.
  - Ensure enough storage headroom for a full capture cycle.
  - Dry-run the scheduler if it is enabled:
    - `sudo systemctl start healtharchive-schedule-annual-dry-run.service`
    - `sudo journalctl -u healtharchive-schedule-annual-dry-run.service -n 200 --no-pager`

## Where to record outcomes

- **Changelog**: public-facing changes and policy updates.
- **Impact report**: monthly coverage + reliability + usage snapshot.
- **Internal ops log**: optional private notes (date + key checks + issues).

