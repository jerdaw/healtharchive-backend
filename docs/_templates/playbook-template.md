# Playbook: <task title> (operators)

Purpose: one sentence on what this playbook achieves.

This is a short, task-oriented checklist. Keep it procedural, public-safe, and low-toil.

## When to use

- <trigger/condition>
- <trigger/condition>

## Preconditions / access

- Environment: production | staging | local
- Required access: <e.g., Tailscale SSH to VPS as `haadmin`; `sudo` required?>
- Required inputs: <e.g., job ID, year, file path>

## Safety / guardrails

- What could go wrong?
- What should you *not* do during this procedure?
- Any caps/cooldowns/sentinels that must be in place?

## Steps

1) <first command> (what it changes)
2) <second command> (what it changes)
3) <…>

## Verification (“done” criteria)

- <health check or expected output>
- <service status / drift check / smoke test>

## Rollback / recovery (if needed)

- <how to back out safely>

## References

- Canonical runbook/checklist: <link>
- Related playbooks: <link>
- Relevant incident note(s): <link>
