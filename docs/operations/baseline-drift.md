# Production baseline drift checks (internal)

Goal: avoid “configuration drift” where production stops matching what the project expects
(security posture, perms, service units, etc.).

This is implemented as:

1) **Desired state (in git)**: `production-baseline-policy.toml`  
2) **Observed state (generated on the VPS)**: JSON snapshots written to `/srv/healtharchive/ops/baseline/`  
3) **Drift check**: compares observed vs policy and fails on required mismatches

## Files

- Policy (edit in git): `production-baseline-policy.toml`
- Snapshot generator: `../../scripts/baseline_snapshot.py`
- Drift checker: `../../scripts/check_baseline_drift.py`

## One-shot usage (recommended after any production change)

On the VPS (as `haadmin`):

```bash
cd /opt/healtharchive-backend
./scripts/check_baseline_drift.py --mode live
```

This writes:

- `observed-<timestamp>.json` (machine-readable)
- `drift-report-<timestamp>.txt` (human-readable)
- plus `observed-latest.json` and `drift-report-latest.txt`

All files live under `/srv/healtharchive/ops/baseline/`.

## “Local only” mode (no network dependency)

Use local-only mode when you want checks that don’t depend on DNS/TLS/external routing:

```bash
./scripts/check_baseline_drift.py --mode local
```

In `local` mode:

- HSTS is validated by parsing `/etc/caddy/Caddyfile` for the API site block.
- Admin endpoint checks are skipped (warn-only).

## CORS validation

The policy enforces a **strict** production allowlist (no extra origins) via
`HEALTHARCHIVE_CORS_ORIGINS`.

- `--mode local` validates the env file value (CSV set comparison).
- `--mode live` additionally probes the API with an `Origin:` header and checks
  real `Access-Control-Allow-Origin` behavior.

## When to update policy

Update `production-baseline-policy.toml` only when you intentionally change production invariants:

- URL strategy (adding staging, changing canonical domains)
- security posture (HSTS policy, admin auth policy)
- directory layout / ownership model
- systemd service names or enablement expectations

Avoid adding “things that change often” to policy (package versions, job counts, etc.).
