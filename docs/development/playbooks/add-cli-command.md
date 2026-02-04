# Playbook: Add a new CLI command (developers)

Purpose: add a backend CLI command that is testable, documented, and safe to operate.

## When to use

- You need a new `ha-backend <command>` for an operator or contributor workflow.
- You need to extend an existing command in a way that changes its contract.

## Preconditions

- You can run the CLI locally (see: `../dev-environment-setup.md` and `../live-testing.md`).
- You understand whether the command is:
  - **developer-only**, or
  - an **operator command** (needs docs under `docs/operations/**` / `docs/deployment/**` and careful safety rails).

## Safety / guardrails

- Avoid adding “powerful defaults” (e.g., destructive operations) without explicit flags and clear output.
- Don’t log secrets (DB URLs, tokens, credentials).
- If this command will be used on production, ensure it has:
  - dry-run support where reasonable,
  - clear “what it changes” output,
  - and tests for key edge cases.

## Steps

1) Implement the command:
   - Add/extend the CLI wiring in `../../../src/ha_backend/cli.py`.
2) Add tests close to the behavior:
   - Prefer `tests/test_cli_*.py` style coverage for parsing and side-effects.
3) Document the command:
   - Developer-only: add to `../live-testing.md` or an appropriate dev doc.
   - Operator-facing: add to a playbook/runbook under `docs/operations/**` or `docs/deployment/**`.
4) Run the local checks:
   - `make ci`

## Verification (“done” criteria)

- Command appears in `ha-backend --help` and `ha-backend <command> --help`.
- Tests cover the expected behavior and key failure modes.
- `make ci` passes.

## References

- Local testing flows: `../live-testing.md`
- Documentation policy: `../../documentation-guidelines.md`
- CLI implementation: `../../../src/ha_backend/cli.py`
