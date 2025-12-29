# WARC storage tiering (SSD + Storage Box)

Goal: keep HealthArchive running on a small VPS SSD by tiering large WARC job
directories onto a Hetzner Storage Box (“cold” storage), while still being able
to replay pages from cold storage.

This playbook assumes:

- Production-like host paths (`/srv/healtharchive/**`)
- Existing snapshots may already reference absolute paths under
  `/srv/healtharchive/jobs/**`
- You want to keep **paths stable** (so replay keeps working) while relocating
  bytes to cheaper storage.

## Architecture (what runs where)

- **VPS (hot / canonical paths)**
  - Canonical archive root: `/srv/healtharchive/jobs`
  - Backend services read WARCs from paths recorded in the DB (often absolute
    paths under `/srv/healtharchive/jobs/**`).
  - For tiering, we keep these canonical paths intact and *mount/bind* cold data
    into them.

- **Storage Box (cold bytes)**
  - Mounted on the VPS at: `/srv/healtharchive/storagebox`
  - Cold mirror root (suggested): `/srv/healtharchive/storagebox/jobs`
  - You store large job directories here and then bind-mount them into the
    canonical paths under `/srv/healtharchive/jobs/**`.

## Create the Storage Box (Hetzner console)

Recommended choices for HealthArchive:

- **Plan**: `BX11` (1 TB) is a good starting tier for cold WARCs.
- **Location**: same region as the VPS.
- **Access**: SSH key auth (recommended).
- **Additional settings**
  - Enable: `SSH Support`
  - Disable (not needed): `SMB Support`, `WebDAV Support`
  - External reachability: prefer **disabled** (you can access via the VPS; no
    need to expose to the public internet).
- **Labels**: optional; if you use them, keep them simple:
  - `project=healtharchive`
  - `role=warc-cold-storage`
  - `env=prod`

Notes:

- “Set as default key” in Hetzner means “preselect this key for future Storage
  Boxes by default” (it doesn’t change the key material).
- On the VPS, ensure private keys are locked down:
  - `chmod 700 ~/.ssh`
  - `chmod 600 ~/.ssh/hetzner_storagebox`
  - `chmod 644 ~/.ssh/hetzner_storagebox.pub`

## Mount the Storage Box on the VPS (sshfs)

Run these on the VPS:

```bash
sudo apt-get update
sudo apt-get install -y sshfs
sudo sed -i 's/^#user_allow_other/user_allow_other/' /etc/fuse.conf
sudo mkdir -p /srv/healtharchive/storagebox
```

Mount (SSH runs on port `23` for Storage Boxes):

```bash
GID="$(getent group healtharchive | cut -d: -f3)"
sudo sshfs -p 23 \
  -o IdentityFile=/home/haadmin/.ssh/hetzner_storagebox \
  -o allow_other,default_permissions \
  -o uid="$(id -u haadmin)",gid="${GID}",umask=0027 \
  -o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,kernel_cache \
  uNNNNNN@uNNNNNN.your-storagebox.de:/ \
  /srv/healtharchive/storagebox
```

Sanity check:

```bash
touch /srv/healtharchive/storagebox/_probe && rm /srv/healtharchive/storagebox/_probe
df -h /srv/healtharchive/storagebox
```

Create the cold mirror root:

```bash
mkdir -p /srv/healtharchive/storagebox/jobs/imports
```

### Make the mount persistent (recommended)

If the Storage Box isn’t mounted (e.g., after reboot), tiered paths may fall
back to empty local directories and replay will break. Add a small systemd unit
to mount it on boot.

Use `docs/deployment/systemd/README.md` as the canonical systemd reference.

Example (VPS): `/etc/systemd/system/healtharchive-storagebox-sshfs.service`

```ini
[Unit]
Description=HealthArchive Storage Box mount (sshfs)
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
User=root
Group=root
ExecStart=/usr/bin/sshfs -p 23 -o IdentityFile=/home/haadmin/.ssh/hetzner_storagebox -o allow_other,default_permissions -o uid=1000,gid=999,umask=0027 -o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,kernel_cache uNNNNNN@uNNNNNN.your-storagebox.de:/ /srv/healtharchive/storagebox
ExecStop=/bin/fusermount3 -u /srv/healtharchive/storagebox
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Notes:

- Replace `uid=1000` with `id -u haadmin` and `gid=999` with the `healtharchive`
  group GID from `getent group healtharchive`.
- Keep `allow_other` only if you have enabled `user_allow_other` in
  `/etc/fuse.conf` and you need non-root services to read the mount.

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now healtharchive-storagebox-sshfs.service
systemctl status healtharchive-storagebox-sshfs.service --no-pager
```

## Move a job directory from SSD → Storage Box (safe swap)

This procedure keeps the canonical path stable.

1) Define paths (VPS):

```bash
HOT=/srv/healtharchive/jobs/imports/<job_dir_name>
COLD=/srv/healtharchive/storagebox/jobs/imports/<job_dir_name>
```

2) Copy to cold tier (VPS):

```bash
mkdir -p "$(dirname "$COLD")"
rsync -rltH --info=progress2 --no-owner --no-group --no-perms "$HOT/" "$COLD/"
```

3) Stop services (VPS):

```bash
sudo systemctl stop healtharchive-worker.service
sudo systemctl stop healtharchive-replay.service
sudo systemctl stop healtharchive-api.service
```

4) Swap the canonical path to point at the cold copy (VPS):

```bash
sudo mv "$HOT" "${HOT}.hot-backup"
sudo mkdir -p "$HOT"
sudo mount --bind "$COLD" "$HOT"
```

5) Start services + verify (VPS):

```bash
sudo systemctl start healtharchive-api.service
sudo systemctl start healtharchive-worker.service
sudo systemctl start healtharchive-replay.service

curl -fsS http://127.0.0.1:8001/api/health >/dev/null && echo OK
```

6) If everything is OK, delete the backup (VPS):

```bash
sudo rm -rf "${HOT}.hot-backup"
```

Rollback (if needed):

```bash
sudo umount "$HOT"
sudo rm -rf "$HOT"
sudo mv "${HOT}.hot-backup" "$HOT"
```

## Promote (cold → hot) later (optional)

If you decide a job should be “hot” again:

1) Stop services.
2) `umount` the canonical path.
3) `rsync` cold → hot (SSD).
4) Start services.

This is the inverse of the “safe swap” above.

## Preflight implications

- If you intend the upcoming annual campaign outputs to land on the Storage Box,
  run preflight with:
  - `./scripts/vps-preflight-crawl.sh --year <YYYY> --campaign-archive-root /srv/healtharchive/storagebox/jobs`
- Ensure the Storage Box mount is active before preflight and before the annual
  campaign runs.

## Annual campaign with a tiny SSD (operational pattern)

If the campaign won’t fit on SSD:

1) Keep the Storage Box mounted.
2) Schedule the annual jobs (dry-run first), then create a cold output directory
   and bind-mount it into the **canonical** job output directory before the
   worker runs the job.

Sketch:

```bash
# After jobs exist (queued), get the job output dir:
/opt/healtharchive-backend/.venv/bin/ha-backend show-job --id <JOB_ID> | rg output_dir

# Suppose output_dir is:
HOT=/srv/healtharchive/jobs/hc/20260101T000000Z__hc-2026

# Create a matching cold location and mount it into place:
COLD=/srv/healtharchive/storagebox/jobs/hc/20260101T000000Z__hc-2026
mkdir -p "$COLD"
sudo mount --bind "$COLD" "$HOT"
```

This keeps DB WARC paths under `/srv/healtharchive/jobs/**` (stable), while the
bytes live on Storage Box.

## Common pitfalls

- `mkdir: Permission denied` under `/srv/healtharchive/storagebox/**`:
  - Your sshfs mount is mapped to the wrong UID/GID; remount with
    `uid=$(id -u haadmin)`, `gid=$(getent group healtharchive | cut -d: -f3)`,
    and a restrictive `umask`.
- `rsync ... chgrp failed: Permission denied`:
  - Use `--no-owner --no-group --no-perms` for cross-filesystem copies to
    Storage Box.
- Storage Box not mounted (but directories still exist):
  - The filesystem checks will silently target the SSD unless you validate the
    mount; keep the mount persistent and verify with `mount | grep storagebox`.
