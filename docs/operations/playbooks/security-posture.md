# Security posture playbook (operators)

Goal: keep the public surface safe-by-default and avoid accidental exposure.

Canonical references:

- Production runbook: `../../deployment/production-single-vps.md`
- Hosting checklist (TLS/HSTS): `../../deployment/hosting-and-live-server-to-dos.md`
- Env wiring + CORS: `../../deployment/environments-and-configuration.md`
- Admin verification: `./scripts/verify-security-and-admin.sh`

## Secrets discipline (always)

- Store secrets only in VPS/Vercel env (or a secret manager), never in git.
  - `HEALTHARCHIVE_ADMIN_TOKEN`
  - DB URL/password
  - Healthchecks ping URLs

## HTTPS + HSTS (API)

- Maintain HSTS at the reverse proxy (Caddy) for `api.healtharchive.ca`.
- After changes, verify HSTS is present:
  - `./scripts/verify-security-and-admin.sh --api-base https://api.healtharchive.ca --require-hsts`

## Strict CORS allowlist (API)

- Keep `HEALTHARCHIVE_CORS_ORIGINS` narrow.
- Treat widening CORS as a deliberate decision (and re-verify headers).
- Verify real headers from production (example):
  - `curl -sS -D- -o /dev/null -H 'Origin: https://healtharchive.ca' https://api.healtharchive.ca/api/health | rg -i '^access-control-allow-origin:'`

## What “done” means

- Admin endpoints are not publicly accessible.
- HSTS is present on `https://api.healtharchive.ca/api/health`.
- CORS behavior matches the allowlist policy.
