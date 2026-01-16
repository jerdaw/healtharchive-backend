# Future roadmap (backlog)

This file tracks **not-yet-implemented** work and planned upgrades.

It is intentionally **not** an implementation plan.

## How to use this file (workflow)

1. Pick a small set of items from this backlog.
2. Create a focused implementation plan in `docs/roadmaps/` (example name: `YYYY-MM-<topic>.md`).
3. Implement the work.
4. Update canonical documentation so operators/users can run and maintain the result.
5. Move the completed implementation plan to `docs/roadmaps/implemented/` and date it.

## External / IRL work (not implementable in git)

These items are intentionally “external” and require ongoing human follow-through.

- External outreach + verification execution (operator-only):
  - Playbook: `../operations/playbooks/outreach-and-verification.md`
- Secure at least 1 distribution partner (permission to name them publicly).
- Secure at least 1 verifier (permission to name them publicly).
- Maintain a public-safe mentions/citations log with real entries:
  - `../operations/mentions-log.md` (links only; no private contact data)
- Healthchecks.io alignment: keep systemd timers, `/etc/healtharchive/healthchecks.env`, and the Healthchecks UI in sync.
  - See: `../operations/playbooks/healthchecks-parity.md` and `../deployment/production-single-vps.md`

Track the current status and next actions in:

- `../operations/healtharchive-ops-roadmap.md`

Supporting materials:

- `../operations/outreach-templates.md`
- `../operations/partner-kit.md`
- `../operations/verification-packet.md`

## Transparency & public reporting (policy posture)

- Incident disclosure posture (current default: Option B):
  - Publish public-safe notes only when an incident changes user expectations (outage/degradation, integrity risk, security posture, policy change).
  - Decision record: `../decisions/2026-01-09-public-incident-disclosure-posture.md`
  - Revisit later: consider moving to “Option A” (always publish public-safe notes for sev0/sev1) once operations are demonstrably stable over multiple full campaign cycles.

## Technical backlog (candidates)

Keep this list short; prefer linking to the canonical doc that explains the item.

### Search & relevance (backend)

- Search ranking + snippet quality iteration (active plan):
  - `2026-01-03-search-ranking-and-snippets-v3.md`
  - Supporting docs/scripts: `../operations/search-quality.md`, `../operations/search-golden-queries.md`, `../../scripts/search-eval-run.sh`
- Authority signals for relevance (optional): outlinks / page signals feeding into ranking and/or tie-breakers.
  - See: `../operations/search-quality.md` (“Backfill outlinks + authority signals”)

### Storage & retention (backend)

- Same-day dedupe path (storage-only optimization; provenance-preserving).
  - Requirements: dry-run mode, reversible/auditable log, and strict invariants (e.g., “same URL, same day, identical `Snapshot.content_hash`”).
  - See: `../operations/search-quality.md`, `../operations/growth-constraints.md`
- Storage/retention upgrades (only with a designed replay retention policy).
  - See: `../operations/growth-constraints.md`, `../deployment/replay-service-pywb.md`

### Ops surface / environments (optional)

- Consider whether a separate staging backend is worth it (increases ops surface; only do if it buys real safety).
  - See: `../deployment/environments-and-configuration.md`
- Tiering health alerting:
  - Enable `healtharchive-tiering-metrics.timer` on the VPS and add alerts on `healtharchive_tiering_*` metrics.
  - This closes the loop so `healtharchive-warc-tiering.service` failures are visible quickly (not just in `systemctl status`).

### Repo governance (future)

- Tighten GitHub merge discipline when there are multiple committers (PR-only + required checks).
  - See: `../operations/monitoring-and-ci-checklist.md`

## Adjacent / optional (in this monorepo, not core HA)

- `rcdc/CDC_zim_mirror`: add startup DB sanity checks and clearer failure modes (empty/invalid LevelDB, missing prefixes, etc.).
