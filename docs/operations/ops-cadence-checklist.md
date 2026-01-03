# Ops Cadence Checklist (internal)

Purpose: make routine operations repeatable and low-friction so the project can be maintained without heroics.

This checklist is intentionally short. If a task feels too heavy to do regularly, it should be moved to a longer cadence or automated safely.


## Every deploy (always)

- **Treat green `main` as the deploy gate** (run local checks, push, wait for CI).
- **Deploy using the VPS helper** (safe deploy + verification):
  - `cd /opt/healtharchive-backend && ./scripts/vps-deploy.sh --apply --baseline-mode live`
- **Verify observability is still healthy** (internal; loopback-only):
  - `cd /opt/healtharchive-backend && ./scripts/vps-verify-observability.sh`
- If the deploy script fails, **don’t retry blindly**:
  - read the drift report / verifier output
  - fix the underlying mismatch (policy vs reality)

Related docs:

- Deploy runbook: `../deployment/production-single-vps.md`
- Verification/monitoring: `monitoring-and-ci-checklist.md`
## Weekly (10–15 minutes)

- **Observability sanity check**
  - `cd /opt/healtharchive-backend && ./scripts/vps-verify-observability.sh`
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

## Ongoing automation maintenance

- Keep systemd unit templates installed/updated on the VPS after repo updates:
  - `cd /opt/healtharchive-backend && sudo ./scripts/vps-install-systemd-units.sh --apply --restart-worker`
- Treat sentinel files under `/etc/healtharchive/` as the explicit on/off controls for automation.
- If you enable Healthchecks pings, keep ping URLs only in the root-owned VPS env file:
  - `/etc/healtharchive/healthchecks.env` (never commit ping URLs)
- If you enable optional automations (coverage guardrails, replay smoke, cleanup), confirm their timers + sentinels are intentional.

See: `../deployment/systemd/README.md`

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
- **Dataset release integrity**
  - Confirm a dataset release exists for the expected quarter/date.
  - Verify checksums: `sha256sum -c SHA256SUMS` (see `dataset-release-runbook.md`).
- **Adoption signals entry** (public-safe)
  - Add a dated entry under `/srv/healtharchive/ops/adoption/` (links + aggregates only).
- **Automation posture check**
  - On the VPS run: `cd /opt/healtharchive-backend && ./scripts/verify_ops_automation.sh`
  - Optional (diff-friendly): `./scripts/verify_ops_automation.sh --json | python3 -m json.tool`
  - Spot-check logs: `journalctl -u <service> -n 200`
- **Growth constraints review**
  - Revisit `growth-constraints.md` (storage, source caps, performance budgets).
  - Adjust only if you can still support the new limits.

## Annual (before Jan 01 UTC)

- **Annual edition readiness**
  - Review `annual-campaign.md` for scope changes.
  - Ensure enough storage headroom for a full capture cycle.
  - Run the crawl preflight audit:
    - `cd /opt/healtharchive-backend && YEAR=2026 && ./scripts/vps-preflight-crawl.sh --year "$YEAR"`
  - Dry-run the scheduler if it is enabled:
    - `sudo systemctl start healtharchive-schedule-annual-dry-run.service`
    - `sudo journalctl -u healtharchive-schedule-annual-dry-run.service -n 200 --no-pager`

## Where to record outcomes

- **Changelog**: public-facing changes and policy updates.
- **Impact report**: monthly coverage + reliability + usage snapshot.
- **Internal ops log**: optional private notes (date + key checks + issues).
