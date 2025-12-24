# Observability scaffolding playbook (private; VPS)

Goal: prepare the **filesystem + secrets layout** for private observability
without installing any new services yet.

Canonical reference for boundaries (read first):

- `../observability-and-private-stats.md`

## Preconditions

- You are on the production VPS and can `sudo`.
- `/srv/healtharchive/` exists.
- The ops group exists (usually `healtharchive`).

## Procedure

1. From the repo on the VPS:

   - `cd /opt/healtharchive-backend`

2. Run the bootstrap script:

   - `sudo ./scripts/vps-bootstrap-observability-scaffold.sh`

3. Populate secret files (do **not** store secrets under `/srv/healtharchive/ops/`):

   - `sudoedit /etc/healtharchive/observability/prometheus_backend_admin_token`
   - `sudoedit /etc/healtharchive/observability/grafana_admin_password`
   - `sudoedit /etc/healtharchive/observability/postgres_grafana_password`

   Notes:

   - These files are created root-only (`0600`) by default.
   - Later installation steps may adjust permissions so services can read them.

## Verify

- Confirm directories exist and have expected ownership/modes:
  - `stat -c '%U:%G %a %n' /srv/healtharchive/ops/observability /srv/healtharchive/ops/observability/*`
- Confirm secret files exist and are root-only:
  - `stat -c '%U:%G %a %n' /etc/healtharchive/observability/*`

## Rollback

- Remove the scaffolding directories:
  - `sudo rm -rf /srv/healtharchive/ops/observability`
- Remove the secret files:
  - `sudo rm -rf /etc/healtharchive/observability`
