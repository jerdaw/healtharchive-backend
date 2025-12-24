# Annual campaign playbook (operators)

Goal: keep the annual capture cycle predictable and operationally boring.

Canonical references:

- Annual scope/seeds (source of truth): `../annual-campaign.md`
- Automation implementation plan: `../automation-implementation-plan.md`
- systemd units + enablement: `../../deployment/systemd/README.md`

## Before Jan 01 UTC (readiness)

- Review and update scope/seeds in `../annual-campaign.md` (docs-only change).
- Ensure you have storage headroom and backups are healthy.
- If the annual scheduler is enabled, dry-run it:
  - `sudo systemctl start healtharchive-schedule-annual-dry-run.service`
  - `sudo journalctl -u healtharchive-schedule-annual-dry-run.service -n 200 --no-pager`

## During/after the campaign (high level)

- Use the automation plan (`../automation-implementation-plan.md`) to decide what is enabled and what is manual.
- Prefer safe, idempotent entrypoints (systemd services/timers or the provided scripts).

## What “done” means

- Annual scope is current in `../annual-campaign.md`.
- If automation is enabled, the scheduler and follow-up tasks run as intended and are verifiable in logs/artifacts.

