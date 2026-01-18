# Runbook: <system / workflow> (operators)

Purpose: one paragraph describing what this runbook covers and when it is the canonical reference.

## Scope

- Environment(s): production | staging | dev
- Audience: operator | developer | both
- Non-goals: what this runbook explicitly does not cover

## Preconditions

- Required access (Tailscale, SSH user, `sudo`, secrets location)
- Required inputs (env vars, paths, hostnames, domains)
- Required dependencies (packages, services)

## Architecture / topology (short)

- Components involved (API, worker, DB, reverse proxy, storage)
- Network posture (public ports vs loopback-only vs tailnet-only)
- Data paths (where state and artifacts live)

## Procedure

### 1) <Step group title>

```bash
<command>
```

What this changes:

- <state change>

### 2) <Step group title>

```bash
<command>
```

What this changes:

- <state change>

## Verification (“done” criteria)

- Public surface: <what to check>
- Internal health: <services, logs, metrics>
- Drift / policy checks: <baseline drift, config invariants>

## Rollback / recovery

- Safe rollback strategy (fast path)
- What to avoid (data integrity risks)

## Troubleshooting

- Common failures and the first 1–3 commands to triage
- Pointers to deeper playbooks and incident response

## References

- Related playbooks: <link>
- Related checklists: <link>
- Related incident notes: <link>
