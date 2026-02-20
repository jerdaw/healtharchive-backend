# Runbook: VPS Backend Deployment (operators)

Purpose: This runbook provides the canonical workflow for deploying the latest `healtharchive-backend` and enabling automated incident recovery (drift auto-reconcile).

## Scope

- Environment: production (single VPS)
- Audience: operator
- Non-goals: This does not cover the initial VPS setup/provisioning (see [production-single-vps.md](production-single-vps.md)).

## Preconditions

- Required access: Tailscale access to the host, `haadmin` user with `sudo` permissions.
- Required inputs: No new secrets required for standard deploys.
- Required dependencies: `git`, `docker`, `python3-venv` (already on production VPS).

## Architecture / topology (short)

- **Backend**: FastAPI (API) + Python Worker.
- **Reverse Proxy**: Caddy (managed separately).
- **Watchdogs**: `vps-crawl-auto-recover.py`, `vps-storage-hotpath-auto-recover.py`, and the new `vps-drift-auto-reconcile.py`.

## Procedure

### 1) SSH to the VPS

Connect using the Tailscale IP or host alias:

```bash
ssh haadmin@vps-tailscale-alias
```

### 2) Run the Deployment Script

Navigate to the repo root and run the safe-by-default deploy helper:

```bash
cd /opt/healtharchive-backend

# Dry-run (recommended first)
./scripts/vps-deploy.sh

# Apply deployment
./scripts/vps-deploy.sh --apply
```

What this changes:

- Pulls the latest code from `main`.
- Installs/updates Python dependencies in the `.venv`.
- Runs DB migrations (`alembic upgrade head`).
- Restarts the `healtharchive-api` service.
- Restarts `healtharchive-worker` (only if no jobs are actively crawling).

### 3) Enable Auto-Reconciliation Watchdog (One-time)

To prevent future "502 Bad Gateway" errors from missing dependencies, enable the new automated watchdog:

```bash
# Install the new systemd units from the repo
sudo ./scripts/vps-install-systemd-units.sh --apply

# Create the sentinel file to enable the drift watchdog
sudo install -m 0644 -o root -g root /dev/null /etc/healtharchive/drift-auto-reconcile-enabled

# Enable and start the timer
sudo systemctl enable --now healtharchive-drift-auto-reconcile.timer
```

What this changes:

- Installs `healtharchive-drift-auto-reconcile.{service,timer}`.
- Configures the watchdog to run every 5 minutes.
- If it detects "baseline drift" (missing packages), it will auto-trigger a re-deployment to fix the environment.

## Verification (“done” criteria)

- **Public surface**: Verify `https://api.healtharchive.ca/api/health` returns HTTP 200.

- **Internal health**: Check service status:

  ```bash
  sudo systemctl status healtharchive-api healtharchive-worker healtharchive-drift-auto-reconcile.timer
  ```

- **Watchdog Metrics**: If Prometheus/Grafana is setup, verify `healtharchive_drift_auto_reconcile_enabled 1` exists in metrics.

## Rollback / recovery

- **Fast path**: Use `./scripts/vps-deploy.sh --apply --ref <PREVIOUS_SHA>` to revert to a known good version.
- **Watchdog Disable**: To stop the auto-recovery, delete the sentinel file:
  ```bash
  sudo rm /etc/healtharchive/drift-auto-reconcile-enabled
  ```

## Troubleshooting

- **502 Bad Gateway**: Check if the worker/API is dead: `sudo journalctl -u healtharchive-api -n 100`.
- **Deploy Lock**: If the script says a lock is held, check for orphans: `ls -l /tmp/healtharchive-backend-deploy.lock`.

## References

- Full Production Runbook: [production-single-vps.md](production-single-vps.md)
- Systemd README: [docs/deployment/systemd/README.md](systemd/README.md)
