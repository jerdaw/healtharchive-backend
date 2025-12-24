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

- Secure at least 1 distribution partner (permission to name them publicly).
- Secure at least 1 verifier (permission to name them publicly).
- Maintain a public-safe mentions/citations log with real entries (links only; no private contact data).

Track the current status and next actions in:

- `../operations/healtharchive-ops-roadmap.md`

Supporting materials:

- `../operations/outreach-templates.md`
- `../operations/partner-kit.md`
- `../operations/verification-packet.md`

## Technical backlog (candidates)

Keep this list short; prefer linking to the canonical doc that explains the item.

- Tighten GitHub merge discipline when there are multiple committers (PR-only + required checks).
  - See: `../operations/monitoring-and-ci-checklist.md`
- Consider whether a separate staging backend is worth it (optional; increases ops surface).
  - See: `../deployment/environments-and-configuration.md`
- Storage/retention upgrades (only with a designed replay retention policy).
  - See: `../operations/growth-constraints.md`, `../deployment/replay-service-pywb.md`
