# Ops automation verification JSON output (implementation plan)

Status: **implemented** (2026-01-03)

## Goal

Make the production posture check `./scripts/verify_ops_automation.sh` easier to diff and reason about by adding a **JSON output mode** that summarizes:

- which expected timer units exist,
- which are enabled/active,
- sentinel file presence (when applicable),
- worker override presence,
- expected ops directories presence,
- and a top-level pass/fail.

This should preserve the current human-readable default output.

## Scope

- Add a `--json` option to `scripts/verify_ops_automation.sh`.
- Ensure JSON mode writes **JSON to stdout only** (human logs go to stderr, or are suppressed).
- Update ops documentation to mention JSON mode and a suggested diff workflow.

## Non-goals

- Changing which timers are considered required by default.
- Adding new timers or altering systemd unit files.
- Building a separate CI job to run this (it is intended for the VPS).

## Implementation steps

1. Extend `scripts/verify_ops_automation.sh`:
   - Parse `--json`.
   - Collect per-check results into an in-memory summary.
   - Emit a stable JSON object at the end of the run.
   - On hosts without `systemctl`, in JSON mode emit a “skipped” JSON payload and exit `0`.
2. Update canonical ops docs:
   - `docs/operations/automation-verification-rituals.md`
   - `docs/operations/ops-cadence-checklist.md`
3. Update `docs/planning/README.md` to list this plan as active.

## Acceptance criteria

- `./scripts/verify_ops_automation.sh` (default) behaves as before.
- `./scripts/verify_ops_automation.sh --json` prints a single valid JSON object to stdout.
- Exit codes remain compatible:
  - `0` when all required checks pass,
  - `1` when required checks fail.
- JSON includes at least:
  - `timers[]` (name, required, unit_present, enabled_state, active_state, sentinel_path, sentinel_present, meets_required),
  - `worker_override` (path, present, required),
  - `ops_dirs[]` (path, present),
  - `failures`, `warnings`, `ok`.

## Operator usage

- Human mode: `./scripts/verify_ops_automation.sh`
- JSON mode (diff-friendly): `./scripts/verify_ops_automation.sh --json > /srv/healtharchive/ops/automation/posture.json`
- Pretty-print (optional): `./scripts/verify_ops_automation.sh --json | python3 -m json.tool`
