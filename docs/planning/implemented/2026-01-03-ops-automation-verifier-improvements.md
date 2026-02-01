# Ops automation verifier improvements (implementation plan)

Status: **implemented** (2026-01-03)

## Roadmap item (single, prioritized)

Harden `./scripts/verify_ops_automation.sh` so it is:

- easier to run strictly (one flag instead of many),
- easier to consume by automation (clean JSON-only mode + stable schema),
- easier to maintain (single “expected timers” inventory),
- and harder to regress (basic CI test for JSON output invariants).

## Goals

1. Keep human output useful while keeping JSON output truly machine-friendly.
2. Reduce drift by defining expected timers/dirs in one place.
3. Make “strict posture” checks easy for operators.
4. Add minimal regression coverage for JSON output.

## Scope

### Script improvements (`scripts/verify_ops_automation.sh`)

- Add `--quiet` (suppress all human logs) and `--json-only` (implies `--json --quiet`).
- Add convenience flags:
  - `--require-all-present` (fail if any expected timer unit is missing)
  - `--require-all-enabled` (fail if any expected timer is not enabled; implies `--require-all-present`)
- Centralize the expected timers list into a single inventory structure and drive checks from it.
- Emit a concise end-of-run summary in human mode:
  - `failures`, `warnings`, `missing_optional`, `disabled_optional` (best-effort)
- Extend JSON with a `summary` object and (best-effort) `unexpected_timers[]`.

### Docs improvements (ops)

- Document `--json-only` and posture snapshot/diff conventions in:
  - `docs/operations/automation-verification-rituals.md`
  - `docs/operations/ops-cadence-checklist.md`

### Tests (backend)

- Add one pytest test that asserts JSON mode invariants:
  - stdout is a single JSON object line
  - parses successfully
  - includes required top-level keys (`schema_version`, `skipped`, `ok`, `failures`, `warnings`)

## Non-goals

- Changing systemd unit file behavior or timer schedules.
- Expanding the set of production automation units.
- Adding complex script configuration that would weaken posture checks.

## Implementation steps

1. Update `scripts/verify_ops_automation.sh`:
   - add flags and usage text
   - refactor timers into a single expected inventory
   - implement strict flags + summary + JSON-only output
2. Update ops docs to reflect the new flags and recommended posture snapshot workflow.
3. Add the JSON invariants test under `tests/`.
4. Run `make check`.
5. Move this plan to `docs/planning/implemented/` and update the implemented index.

## Acceptance criteria

- Default human output remains readable and exit-code behavior remains compatible.
- `--json` continues to emit JSON to stdout (logs to stderr).
- `--json-only` emits **only** JSON to stdout and nothing else.
- Strict flags work as intended:
  - `--require-all-present` fails on any missing timer unit
  - `--require-all-enabled` fails on any expected timer not enabled
- Tests pass in CI: `make check`.
