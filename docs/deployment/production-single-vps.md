# HealthArchive.ca – Production on a Single VPS (Hetzner + Tailscale)

This is the record of the current production deployment. It is a **single VPS**
that runs Postgres, the API, the worker, Caddy (TLS), and all archive storage.
SSH is private-only via **Tailscale**; the public internet only sees ports
`80/443`.

Use this as the canonical runbook for rebuilding the stack, auditing it, or
explaining it to new operators.

---

## 1) Hosting / topology

- **Provider / size:** Hetzner Cloud, `cx33` (Cost-Optimized, 4 vCPU / 8GB RAM / 80GB SSD)
- **Region:** Nuremberg (cost-optimized not available in US-East at the time)
- **Public services:** `api.healtharchive.ca` on 80/443 via Caddy
- **Replay (optional):** `replay.healtharchive.ca` via Caddy → pywb (see `deployment/replay-service-pywb.md`)
- **Private-only:** SSH on Tailscale (`tailscale0`), no public port 22
- **Storage:**
  - `/srv/healtharchive/jobs` – archive root (WARCs / job outputs)
  - `/srv/healtharchive/backups` – DB dumps
- **Database:** Local Postgres on the VPS
- **Monitoring/alerts:**
  - Healthchecks.io pings for DB backup success/failure
  - Healthchecks.io pings for disk-usage threshold
  - (External uptime checks recommended: `/api/health` and `/archive`)
- **Backups:** Nightly `pg_dump -Fc` → `/srv/healtharchive/backups`, retained 14 days
- **Offsite copy:** Synology NAS pulls backups over Tailscale via rsync/SSH

---

## 2) Provision & OS hardening (Hetzner)

1) Create server:
   - Type: Cost-Optimized, x86, `cx33`
   - Region: Nuremberg
   - OS: Ubuntu 24.04 LTS
   - Attach SSH public key; no password login
2) Hetzner Cloud Firewall (final state):
   - Allow TCP 80, 443 (anywhere)
   - Allow UDP 41641 (anywhere) for Tailscale
   - **No** public TCP 22
3) OS setup:
   - Create `haadmin` (sudo), disable root SSH login, disable SSH passwords
   - Enable `unattended-upgrades`
   - UFW: allow 80/443, allow 22 **only on `tailscale0`**, allow 41641/udp

---

## 3) Runtime dependencies

On the VPS (as `haadmin`):

```bash
sudo apt update
sudo apt -y install docker.io \
  postgresql postgresql-contrib \
  python3 python3-venv python3-pip \
  git curl build-essential pkg-config unzip
sudo systemctl enable --now docker postgresql
```

Notes:

- Docker Compose is optional for this stack. On Ubuntu 24.04, the packaged
  Compose plugin is often `docker-compose-v2` (not `docker-compose-plugin`):

  ```bash
  sudo apt -y install docker-compose-v2
  docker compose version
  ```

Directories:

```bash
sudo groupadd --system healtharchive 2>/dev/null || true
sudo mkdir -p /srv/healtharchive/jobs /srv/healtharchive/backups
sudo chown -R haadmin:haadmin /srv/healtharchive/jobs
sudo chown root:healtharchive /srv/healtharchive/backups
sudo chmod 2770 /srv/healtharchive/backups
```

Postgres:

```bash
sudo -u postgres psql -c "CREATE USER healtharchive WITH PASSWORD '<DB_PASSWORD>';"
sudo -u postgres psql -c "CREATE DATABASE healtharchive OWNER healtharchive;"
```

---

## 4) Backend deploy (API + worker, systemd)

Clone + venv:

```bash
sudo mkdir -p /opt && sudo chown haadmin:haadmin /opt
git clone https://github.com/jerdaw/healtharchive-backend.git /opt/healtharchive-backend
cd /opt/healtharchive-backend
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e ".[dev]" "psycopg[binary]"
```

Env file (root-owned, group-readable):

```bash
sudo groupadd --system healtharchive 2>/dev/null || true
sudo usermod -aG healtharchive haadmin
sudo install -d -m 750 -o root -g healtharchive /etc/healtharchive
sudo tee /etc/healtharchive/backend.env >/dev/null <<'EOF'
HEALTHARCHIVE_ENV=production
HEALTHARCHIVE_DATABASE_URL=postgresql+psycopg://healtharchive:<DB_PASSWORD>@127.0.0.1:5432/healtharchive
HEALTHARCHIVE_ARCHIVE_ROOT=/srv/healtharchive/jobs
HEALTHARCHIVE_ADMIN_TOKEN=<LONG_RANDOM_TOKEN>
HEALTHARCHIVE_CORS_ORIGINS=https://healtharchive.ca,https://www.healtharchive.ca,https://healtharchive.vercel.app
HEALTHARCHIVE_LOG_LEVEL=INFO
HA_SEARCH_RANKING_VERSION=v2
HA_PAGES_FASTPATH=1

# Optional: replay integration (pywb). Enables `browseUrl` fields in the public API.
# HEALTHARCHIVE_REPLAY_BASE_URL=https://replay.healtharchive.ca

# Optional: cached replay preview images (homepage thumbnails for /archive cards).
# HEALTHARCHIVE_REPLAY_PREVIEW_DIR=/srv/healtharchive/replay/previews
EOF
sudo chown root:healtharchive /etc/healtharchive/backend.env
sudo chmod 640 /etc/healtharchive/backend.env
```

Migrate + seed:

```bash
set -a; source /etc/healtharchive/backend.env; set +a
./.venv/bin/alembic upgrade head
./.venv/bin/ha-backend seed-sources
./.venv/bin/ha-backend recompute-page-signals
./.venv/bin/ha-backend rebuild-pages --truncate
```

Systemd services:

- API: `/etc/systemd/system/healtharchive-api.service`
  - `ExecStart=/opt/healtharchive-backend/.venv/bin/uvicorn ha_backend.api:app --host 127.0.0.1 --port 8001`
  - `EnvironmentFile=/etc/healtharchive/backend.env`
- Worker: `/etc/systemd/system/healtharchive-worker.service`
  - `ExecStart=/opt/healtharchive-backend/.venv/bin/ha-backend start-worker --poll-interval 30`

Optional systemd automation (recommended):

- Annual scheduling timer (Jan 01 UTC) + worker priority drop-in:
  - Templates + install steps: `deployment/systemd/README.md`

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now healtharchive-api healtharchive-worker
curl -i http://127.0.0.1:8001/api/health
```

---

## 5) HTTPS + DNS (Caddy)

1) DNS (Namecheap): `A api.healtharchive.ca -> <VPS_PUBLIC_IP>`
2) Install Caddy: `sudo apt -y install caddy`
3) Caddyfile: `/etc/caddy/Caddyfile`

```caddyfile
api.healtharchive.ca {
  reverse_proxy 127.0.0.1:8001
}
```

4) Validate + reload:

```bash
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Verify:

```bash
curl -i https://api.healtharchive.ca/api/health
```

---

## 5.1) Optional: replay service (pywb)

Full-fidelity browsing (CSS/JS/images) requires a replay engine. If you want
“click links and stay inside the archived backup”, deploy pywb behind Caddy:

- Runbook: `deployment/replay-service-pywb.md`

Operational warning:

- `ha-backend cleanup-job --mode temp` removes temp dirs **including WARCs**.
  Replay depends on WARCs staying on disk, so do not run cleanup for any job
  you intend to keep replayable.
  If replay is enabled globally (`HEALTHARCHIVE_REPLAY_BASE_URL` is set),
  `cleanup-job --mode temp` will refuse unless you pass `--force`.

Optional UX improvement:

- If `HEALTHARCHIVE_REPLAY_PREVIEW_DIR` is configured, the API can serve cached
  PNG “homepage previews” used by the frontend on `/archive`.
  See `deployment/replay-service-pywb.md` (“Cached source preview images”) for
  the generation command.

---

## 6) Tailscale (SSH/private access only)

- Installed on VPS, NAS, and admin workstation.
- VPS Tailscale IP: `100.x.y.z` (example)
- SSH only allowed on `tailscale0` in UFW; public port 22 blocked at Hetzner.
- Hetzner firewall adds UDP 41641 for better Tailscale connectivity.
- Recommended: disable Tailscale key expiry for the VPS and NAS devices in the
  Tailscale admin UI so access does not silently expire.

Usage:

```bash
ssh -i ~/.ssh/healtharchive_hetzner haadmin@100.x.y.z
```

Public SSH:
- Expected to **fail**: `ssh haadmin@api.healtharchive.ca` (closed).

---

## 7) Backups + NAS pull (rsync over Tailscale)

VPS backup user:
- `habackup` user with NAS public key in `/home/habackup/.ssh/authorized_keys`

Backup script: `/usr/local/bin/healtharchive-db-backup`
- `pg_dump -Fc` to `/srv/healtharchive/backups/healtharchive_<ts>.dump`
- 14-day retention
- Healthchecks `/start`/`/fail`/success pings (see §8)

Systemd:
- `/etc/systemd/system/healtharchive-db-backup.service`
- `/etc/systemd/system/healtharchive-db-backup.timer` (daily ~03:30 UTC, randomized delay)

NAS pull:
- NAS key: `~/.ssh/ha_backup_nas` (no passphrase)
- SSH config alias on NAS:

```sshconfig
Host ha-vps
  HostName 100.x.y.z
  User habackup
  IdentityFile ~/.ssh/ha_backup_nas
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
```

- Rsync command (used manually + DSM scheduled task):

```bash
rsync -av --delete ha-vps:/srv/healtharchive/backups/ /volume1/nobak/healtharchive/backups/db/
```

---

## 8) Healthchecks.io (backup + disk)

Secrets file: `/etc/healtharchive/healthchecks.env` (mode 600)

```
HC_DB_BACKUP_URL=<healthchecks_db_ping>
HC_DISK_URL=<healthchecks_disk_ping>
HC_DISK_THRESHOLD=80
```

Disk check:
- Script: `/usr/local/bin/healtharchive-disk-check`
- Service/Timer: `healtharchive-disk-check.service` / `healtharchive-disk-check.timer` (hourly)
- Pings success; sends `/fail` if `/` or `/srv/healtharchive` exceeds 80%.

---

## 9) Synthetic snapshot for smoke testing

Created a minimal WARC + Snapshot for smoke checks:
- WARC: `/srv/healtharchive/jobs/manual-warcs/viewer-test.warc.gz`
- Snapshot ID: `1`
- Raw: `https://api.healtharchive.ca/api/snapshots/raw/1`
- Viewer: `https://www.healtharchive.ca/snapshot/1`

Use this to verify end-to-end viewer behavior after deploys.

---

## 10) Restore drill (completed)

Procedure:

```bash
latest="$(ls -t /srv/healtharchive/backups/healtharchive_*.dump | head -n 1)"
sudo -u postgres dropdb --if-exists healtharchive_restore_test
sudo -u postgres createdb healtharchive_restore_test
sudo -u postgres pg_restore --no-owner --no-acl -d healtharchive_restore_test < "$latest"
sudo -u postgres psql -d healtharchive_restore_test -c "select count(*) from snapshots;"
sudo -u postgres dropdb healtharchive_restore_test
```

Result: restore succeeded, `snapshots` contained 1 row (the synthetic test snapshot).

---

## 11) External uptime checks (recommended)

Configure an external monitor (e.g., UptimeRobot) for:
- `https://api.healtharchive.ca/api/health`
- `https://www.healtharchive.ca/archive`

Note: some providers use `HEAD` by default; the backend supports `HEAD /api/health`.

---

## 12) Current known defaults/assumptions (2025-12)

- CORS allowlist: `https://healtharchive.ca`, `https://www.healtharchive.ca`, `https://healtharchive.vercel.app`
- Vercel envs set to use `https://api.healtharchive.ca` for both Preview and Production
- No staging backend; Preview and Production frontends point to the same API
- Public SSH closed; Tailscale required for admin/backup access
