# Importing legacy crawls (from old `.zim` backups)

This doc explains how we imported **existing crawl data** (originally used to
produce `.zim` files) into the **HealthArchive backend** so it shows up in:

- `https://api.healtharchive.ca` (search, sources, viewer)
- `https://www.healtharchive.ca/archive` (live results)

## Important clarification: we did not “import the `.zim` files”

The backend does **not** read `.zim` files today.

Instead, we imported the **WARC files** (the raw web captures) that live next to
those `.zim` outputs in the old crawl directories. The backend indexes WARCs
into database rows (`Snapshot`) and the snapshot viewer replays archived HTML
from those WARCs.

If you want to keep the `.zim` files, store them as separate artifacts (NAS /
object storage). They’re useful for offline viewing, but they are not part of
the backend’s serving path.

## Terminology

- **Legacy crawl**: a crawl run before this project’s integrated backend existed.
- **WARC**: compressed web capture files (`*.warc.gz`).
- **Import directory**: a directory on the VPS under
  `/srv/healtharchive/jobs/imports/<import-name>` that holds the legacy WARCs in
  a layout the backend’s WARC discovery can find.
- **Register**: create an `ArchiveJob` row pointing at an existing directory
  (`ha-backend register-job-dir`).
- **Index**: parse WARCs and write `Snapshot` rows (`ha-backend index-job`).

## Prerequisites

- Backend is already deployed on the VPS and can reach Postgres.
- You have an **SSH path** from your NAS to the VPS:
  - We used **Tailscale** so the NAS can reach the VPS consistently even if home
    IP changes.
  - We used a dedicated NAS SSH key and a dedicated VPS user (`habackup`) for
    file transfer.
- The backend env file exists on the VPS:
  - `/etc/healtharchive/backend.env` (mode `0600`, owned by `root:root`).
- The backend archive root exists:
  - `/srv/healtharchive/jobs`

## Source of truth locations (NAS vs dev VM mount)

Your Synology NAS stores the legacy crawl data at:

- `/volume1/nobak/gov-health-archives/...`

Your Linux dev VM mounts that NAS path at:

- `/mnt/nasd/nobak/gov-health-archives/...`

When writing instructions for “run on NAS”, use the `/volume1/...` path.
When running on the dev VM, use the `/mnt/nasd/...` path.

## Step-by-step: importing a legacy dataset

### Step 1 — Identify the WARC archive directory to import

In the legacy crawl output, find the directory that contains the WARCs.
Example (Health Canada legacy crawl):

- NAS:
  - `/volume1/nobak/gov-health-archives/canada_ca_health_backup_2025-04-21/crawler_data/collections/canada_ca_health_crawl_2025-04/archive/`
- Dev VM (same content, mounted):
  - `/mnt/nasd/nobak/gov-health-archives/canada_ca_health_backup_2025-04-21/crawler_data/collections/canada_ca_health_crawl_2025-04/archive/`

What you’re looking for:

- many files like `rec-<id>-<collection>-<timestamp>-N.warc.gz`

### Step 2 — Create the destination “import directory” structure on the VPS

We created an import output directory that looks like an `archive_tool` output,
because the backend’s WARC discovery already knows how to find WARCs inside a
job directory by looking for temp dirs like `.tmp-*` and `collections/*/archive`.

Example destination (Health Canada legacy import):

- `/srv/healtharchive/jobs/imports/legacy-hc-2025-04-21/.tmp-legacy/collections/crawl-legacy-hc-2025-04-21/archive/`

Example destination (CIHR legacy import):

- `/srv/healtharchive/jobs/imports/legacy-cihr-2025-04/.tmp-legacy/collections/crawl-legacy-cihr-2025-04/archive/`

Notes:

- The exact collection directory name doesn’t matter much; what matters is the
  presence of:
  - a `.tmp-*` directory (we used `.tmp-legacy`)
  - a `collections/<anything>/archive/` directory inside it
  - WARCs under that archive directory
- The backend log line you’ll see during indexing is similar to:
  - “Fallback found latest temp dir: …/.tmp-legacy”

### Step 3 — Transfer the WARCs from NAS → VPS via rsync

We ran `rsync` from the NAS, using SSH over Tailscale:

```bash
rsync -av --info=progress2 --partial --append-verify --bwlimit=5000 \
  -e "ssh -i ~/.ssh/<NAS_SSH_KEY>" \
  "/volume1/nobak/gov-health-archives/<LEGACY_PATH>/archive/" \
  "habackup@<VPS_TAILSCALE_IP>:/srv/healtharchive/jobs/imports/<IMPORT_NAME>/.tmp-legacy/collections/<COLLECTION_NAME>/archive/"
```

Why these flags:

- `--partial --append-verify`: safe-ish resume if the connection drops.
- `--bwlimit`: avoids saturating your uplink.

After transfer, verify on VPS:

```bash
sudo find "/srv/healtharchive/jobs/imports/<IMPORT_NAME>" -name '*.warc.gz' | wc -l
sudo du -sh "/srv/healtharchive/jobs/imports/<IMPORT_NAME>"
```

Real example result (Health Canada legacy import):

- `959` WARCs
- `~26G` total

### Step 4 — Normalize permissions (rsync from NAS can create unsafe modes)

The rsync upload preserved permissive modes (`777`) from the source, which is
not what we want on the VPS.

We normalized permissions on the VPS:

```bash
IMPORT_DIR="/srv/healtharchive/jobs/imports/<IMPORT_NAME>"

sudo chown -R habackup:healtharchive "$IMPORT_DIR"

# Directories: rwx for owner+group, setgid so new files inherit group
sudo find "$IMPORT_DIR" -type d -exec chmod 2770 {} +

# WARCs: readable by owner+group only
sudo find "$IMPORT_DIR" -type f -name '*.warc.gz' -exec chmod 640 {} +
```

Why:

- `healtharchive` group can be granted controlled read access.
- We avoid `777` and other “world writable” mistakes.

### Step 5 — Register the directory as an ArchiveJob

Because `/etc/healtharchive/backend.env` is root-owned and not readable by
normal users, we used `systemd-run` to run the CLI with
`EnvironmentFile=/etc/healtharchive/backend.env`.

```bash
sudo systemd-run --wait --pipe \
  --property=EnvironmentFile=/etc/healtharchive/backend.env \
  /opt/healtharchive-backend/.venv/bin/ha-backend register-job-dir \
  --source <SOURCE_CODE> \
  --output-dir "/srv/healtharchive/jobs/imports/<IMPORT_NAME>" \
  --name "<JOB_NAME>"
```

Example (Health Canada legacy import):

- `--source hc`
- `--output-dir /srv/healtharchive/jobs/imports/legacy-hc-2025-04-21`
- `--name legacy-hc-2025-04-21`
- Created `ArchiveJob` ID `1`

### Step 6 — Index WARCs into Snapshot rows

Indexing is the expensive part. It scans WARCs and creates one `Snapshot` row
per captured HTML page.

```bash
sudo systemd-run --wait --pipe \
  --property=EnvironmentFile=/etc/healtharchive/backend.env \
  /opt/healtharchive-backend/.venv/bin/ha-backend index-job \
  --id <JOB_ID>
```

Expected resource usage (real example: Health Canada legacy import):

- `959` WARCs
- `~2h 16m` wall time
- `~1.9G` peak RAM
- CPU pegged near 100% during indexing

You can watch progress indirectly via systemd:

```bash
sudo systemctl status run-uXXX.service --no-pager -l
```

### Step 7 — Verify the import worked (DB + API + frontend)

On VPS (DB/job view):

```bash
sudo systemd-run --wait --pipe \
  --property=EnvironmentFile=/etc/healtharchive/backend.env \
  /opt/healtharchive-backend/.venv/bin/ha-backend show-job --id <JOB_ID>
```

On your laptop (public API):

```bash
curl -s "https://api.healtharchive.ca/api/search?page=1&pageSize=10"
curl -s "https://api.healtharchive.ca/api/sources"
```

In the browser (frontend):

- `https://www.healtharchive.ca/archive` should show a large snapshot count and real results.
- Clicking “View snapshot” should open `https://www.healtharchive.ca/snapshot/<id>` and the embedded content should load from:
  - `https://api.healtharchive.ca/api/snapshots/raw/<id>`

## Known gotcha: capture dates may look wrong (fix requires re-index)

If the indexer fails to parse `WARC-Date`, the backend may fall back to “now”,
making imported snapshots look like they were captured on import day.

When this is fixed in code, you must **re-run indexing for the job** to update
the stored capture timestamps.

## What we imported so far (real outcomes)

### Health Canada legacy import

- Source: legacy Health Canada crawl output (April 2025).
- Imported to VPS under:
  - `/srv/healtharchive/jobs/imports/legacy-hc-2025-04-21`
- Result:
  - `ArchiveJob` ID `1`
  - `959` WARCs
  - `123,656` snapshots indexed
  - API + frontend show live results.

### CIHR legacy import (in progress / next)

- Source: legacy CIHR crawl output (April 2025).
- WARCs transferred to:
  - `/srv/healtharchive/jobs/imports/legacy-cihr-2025-04/.../archive/`
- Next steps:
  - normalize permissions
  - register-job-dir with `--source cihr`
  - index-job

