# HealthArchive – Replay Service (pywb) runbook

This document covers setting up **full‑fidelity web replay** (HTML + CSS/JS/images/fonts) for HealthArchive using a dedicated **pywb** service behind Caddy.

It is intentionally written so a future operator (or LLM) can follow it without needing additional context.

## 0) What this is (and is not)

**What this provides**

- A replay origin: `https://replay.healtharchive.ca`
- Wayback‑style replay from the project’s **WARC files**
- Natural browsing: links stay inside replay, and captured assets (CSS/JS/images) load from the archive when present

**What this does not provide**

- Guaranteed completeness. If a page depends on third‑party CSS/JS/images that were
  not captured into the WARCs, those assets will still be missing at replay time.
- A custom replay UI in pywb itself. HealthArchive provides the primary browsing
  experience via the frontend wrapper pages (see “Backend wiring” below).

## 1) Core decisions (contract)

### 1.1 Collections are per ArchiveJob

- Each `ArchiveJob` becomes a replay “edition”.
- Collection name is **stable and mechanical**:

  - `job-<job_id>` (example: `job-1`)

This makes it easy to generate replay URLs from DB data later.

### 1.2 Replay URL format

We will use pywb’s standard collection routing.

**Replay latest capture** (most common):

```
https://replay.healtharchive.ca/<collection>/<original_url>
```

**List captures** (“calendar” / capture list UI):

```
https://replay.healtharchive.ca/<collection>/*/<original_url>
```

**Replay closest capture to a timestamp** (14-digit UTC `YYYYMMDDhhmmss`):

```
https://replay.healtharchive.ca/<collection>/<timestamp>/<original_url>
```

Where:

- `<collection>` is `job-<job_id>`
- `<timestamp>` is UTC in `YYYYMMDDhhmmss` (14 digits)

Example (latest capture):

```
https://replay.healtharchive.ca/job-1/https://www.canada.ca/en/health-canada.html
```

Note: HealthArchive’s public API generates **timestamp-locked** replay URLs for
snapshots (the `<timestamp>` form) so the viewer stays anchored to the capture
time as you navigate within the backup.

### 1.3 Retention warning: replay depends on WARCs staying on disk

Replay reads from the WARC files referenced by each job.

Important: `ha-backend cleanup-job --mode temp` currently removes archive_tool temp dirs **including WARCs**
(see `src/ha_backend/cli.py:cmd_cleanup_job`).

If you run cleanup on a replayable job, replay will break.

**Operational rule for now:** do not run `cleanup-job --mode temp` for any job you want replayable.

When replay is enabled (backend env var `HEALTHARCHIVE_REPLAY_BASE_URL` is set),
`cleanup-job --mode temp` will refuse to run unless you pass `--force`.

This rule is repeated in:

- `docs/deployment/production-single-vps.md`
- `docs/deployment/hosting-and-live-server-to-dos.md`
- `docs/development/live-testing.md`

## 2) DNS + TLS

Create DNS:

- `A replay.healtharchive.ca -> <VPS_PUBLIC_IP>`

TLS is handled by Caddy automatically once the site block exists.

Before continuing, SSH to the VPS as your admin user (typically over Tailscale):

```bash
ssh -i ~/.ssh/healtharchive_hetzner haadmin@<VPS_TAILSCALE_IP>
```

## 3) VPS directory layout

On the VPS:

- WARCs/job outputs already live under:
  - `/srv/healtharchive/jobs`
- Replay service state (config, collections, indexes) will live under:
  - `/srv/healtharchive/replay`

Create directories:

```bash
sudo mkdir -p /srv/healtharchive/replay
sudo mkdir -p /srv/healtharchive/replay/collections
```

Create a dedicated system user for the replay volume:

```bash
sudo adduser --system --no-create-home --ingroup healtharchive hareplay
```

Recommended perms (important):

```bash
sudo chown -R hareplay:healtharchive /srv/healtharchive/replay
sudo chmod 2770 /srv/healtharchive/replay /srv/healtharchive/replay/collections
```

Why the `hareplay` ownership matters:

- Your WARC files are typically `640` and group-owned by `healtharchive`.
- The pywb container is hardened with `--cap-drop=ALL`, which means “root” in
  the container **cannot bypass** Unix permissions (no `CAP_DAC_OVERRIDE`).
- We will also run the container as the `hareplay` UID/GID explicitly (below),
  so pywb can:
  - write its indexes under `/webarchive` (owned by `hareplay:healtharchive`)
  - read group-readable WARCs under `/warcs` (group `healtharchive`)

## 4) pywb container deployment (systemd + Docker)

We run pywb **only on localhost** (Caddy is the public edge).

### 4.1 Create pywb config

Create `/srv/healtharchive/replay/config.yaml`:

```yaml
debug: false

# We embed replay inside a HealthArchive wrapper UI later; disable pywb’s framed
# replay chrome so the page itself renders “as captured”.
framed_replay: false

# Prefer stable URLs once a capture is resolved.
redirect_to_exact: true

# Optional: expose an aggregate across all on-disk collections at `/all/...`.
# (This is not required for per-job collections like `/job-1/...`.)
# collections:
#   all: $all
```

### 4.2 Create systemd service

Create `/etc/systemd/system/healtharchive-replay.service`:

```ini
[Unit]
Description=HealthArchive replay (pywb)
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
Restart=always
RestartSec=3

# Safety: start clean
ExecStartPre=-/usr/bin/docker rm -f healtharchive-replay
ExecStartPre=/usr/bin/docker pull webrecorder/pywb:2.9.1

# Run on localhost only; Caddy terminates TLS publicly.
ExecStart=/usr/bin/docker run --rm --name healtharchive-replay \
  -p 127.0.0.1:8090:8080 \
  --user <HAREPLAY_UID>:<HEALTHARCHIVE_GID> \
  --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  -v /srv/healtharchive/replay:/webarchive:rw \
  -v /srv/healtharchive/jobs:/warcs:ro \
  webrecorder/pywb:2.9.1

[Install]
WantedBy=multi-user.target
```

Notes:

- `HAREPLAY_UID` comes from `id -u hareplay` (often `110`).
- `HEALTHARCHIVE_GID` comes from `getent group healtharchive` (3rd `:`-separated field).
- We run as `hareplay:healtharchive` to avoid the container needing to
  `useradd`/`su` internally (which fails when `--cap-drop=ALL` removes
  `CAP_SETUID`/`CAP_SETGID`).

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now healtharchive-replay.service
sudo systemctl status healtharchive-replay.service --no-pager
```

Local check (on the VPS):

```bash
curl -I http://127.0.0.1:8090/ | head
```

If `wb-manager reindex` fails with `Permission denied`:

- Double-check:
  - `/srv/healtharchive/replay` is owned by `hareplay:healtharchive` (not `root:healtharchive`)
  - the systemd unit runs with `--user <hareplay_uid>:<healtharchive_gid>`

Then restart:

```bash
sudo chown -R hareplay:healtharchive /srv/healtharchive/replay
sudo systemctl restart healtharchive-replay.service
```

## 5) Caddy config (public HTTPS)

Edit `/etc/caddy/Caddyfile` and add:

```caddyfile
replay.healtharchive.ca {
  encode zstd gzip

  # Replay needs to be embeddable by the HealthArchive frontend.
  # (The frontend wrapper provides the visible banner/controls.)
  header {
    -X-Frame-Options
    Content-Security-Policy "frame-ancestors https://healtharchive.ca https://www.healtharchive.ca"
  }

  reverse_proxy 127.0.0.1:8090
}
```

Validate + reload:

```bash
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Public check (from your laptop):

```bash
curl -I https://replay.healtharchive.ca/ | head
```

## 5.1) Optional: HealthArchive banner on direct replay pages

The HealthArchive frontend provides the primary replay UX via `/snapshot/<id>`
and `/browse/<id>` (header, navigation, disclaimers). Users may still open
`replay.healtharchive.ca` directly in a new tab.

To reduce confusion, you can inject a small HealthArchive banner into pywb’s
**non-framed** replay HTML using pywb’s `custom_banner.html` hook.

Implementation notes:

- The banner is inserted only for non-framed replay (our default).
- When replay is embedded in an iframe, the banner collapses to a minimal UI
  (View diff + Details + Hide) to avoid duplicating the HealthArchive wrapper header.
- When embedded, the script also emits lightweight `postMessage` events with
  the current replay URL/timestamp so the HealthArchive frontend can support
  edition switching while you browse.
- Users can dismiss it via the **Hide** button (stored in `localStorage` on the
  replay origin).

Deploy on the VPS:

```bash
sudo mkdir -p /srv/healtharchive/replay/templates
sudo install -o hareplay -g healtharchive -m 0640 \
  /opt/healtharchive-backend/docs/deployment/pywb/custom_banner.html \
  /srv/healtharchive/replay/templates/custom_banner.html

sudo systemctl restart healtharchive-replay.service
```

Notes:

- The banner script calls the HealthArchive public API from the replay origin
  (for example, `GET /api/replay/resolve`) to resolve the snapshot ID and build
  the correct “back to snapshot” and compare links. Ensure the backend CORS
  allowlist includes `https://replay.healtharchive.ca` when the banner is
  enabled.
- The direct-replay banner is a compact sticky top bar: title, capture date,
  original URL, an always-visible disclaimer line, and action links (View diff,
  Details, All snapshots, Raw HTML, Metadata JSON, Cite, Report issue, Hide).
  “All snapshots” opens a right-aligned popover list.
- The banner uses `XMLHttpRequest` with pywb’s wombat opt-out (`xhr._no_rewrite = true`)
  so API requests are not replay-rewritten. Ensure CORS allows the
  `X-Pywb-Requested-With` header from `https://replay.healtharchive.ca`.
- Production expects the public API to be reachable at `https://api.<apex>` (for
  example, `https://api.healtharchive.ca`). If your deployment instead proxies
  `/api` on the frontend origin, ensure the banner’s API base candidates are
  still valid for your hostnames.
- If you deploy the backend using `./scripts/vps-deploy.sh --apply --restart-replay`,
  the deploy helper will also install `custom_banner.html` and restart the replay
  service as part of that run (single-VPS setup).

Note: the banner can be disabled for screenshot generation by adding a fragment:

```
...#ha_nobanner=1
```

## 6) Create a collection and index a job’s WARCs (no copying)

pywb’s `wb-manager` requires WARC files to exist *in* the collection’s `archive/`
directory. We avoid duplicating data by placing **symlinks** to the real WARC
files (mounted read-only at `/warcs` in the container).

### 6.0 Recommended: use the backend CLI (one command per job)

If the backend and pywb run on the same VPS, you can make a job replayable via:

```bash
sudo systemd-run --wait --pipe \
  --property=EnvironmentFile=/etc/healtharchive/backend.env \
  /opt/healtharchive-backend/.venv/bin/ha-backend replay-index-job --id 1
```

Dry-run (prints actions without changes):

```bash
sudo systemd-run --wait --pipe \
  --property=EnvironmentFile=/etc/healtharchive/backend.env \
  /opt/healtharchive-backend/.venv/bin/ha-backend replay-index-job --id 1 --dry-run
```

### 6.1 Initialize collection for job 1

```bash
sudo docker exec healtharchive-replay wb-manager init job-1
```

### 6.2 Link job 1 WARCs into the collection

1) Determine the job output directory:

```bash
sudo systemd-run --wait --pipe \
  --property=EnvironmentFile=/etc/healtharchive/backend.env \
  /opt/healtharchive-backend/.venv/bin/ha-backend show-job --id 1
```

2) Find WARCs under that output directory:

```bash
OUTPUT_DIR="/srv/healtharchive/jobs/imports/legacy-hc-2025-04-21"  # example; replace
find "$OUTPUT_DIR" -type f -name '*.warc.gz' | sort > /tmp/job-1-warcs.txt
wc -l /tmp/job-1-warcs.txt
```

3) Convert host paths → container paths (because we mount `/srv/healtharchive/jobs` as `/warcs`):

```bash
sed 's#^/srv/healtharchive/jobs#\/warcs#' /tmp/job-1-warcs.txt > /tmp/job-1-warcs.container.txt
```

4) Create symlinks in the collection archive directory (prefixing with a stable counter to avoid name collisions):

```bash
COLL_ARCHIVE_DIR="/srv/healtharchive/replay/collections/job-1/archive"
sudo mkdir -p "$COLL_ARCHIVE_DIR"

nl -ba /tmp/job-1-warcs.container.txt | while read -r n p; do
  printf -v linkname "warc-%06d.warc.gz" "$n"
  sudo ln -sf "$p" "$COLL_ARCHIVE_DIR/$linkname"
done
```

Note: the symlink targets are container paths under `/warcs/...`, so they may
appear “broken” when inspected on the host. They will resolve correctly inside
the container because `/srv/healtharchive/jobs` is mounted as `/warcs`.

5) Index:

```bash
sudo docker exec healtharchive-replay wb-manager reindex job-1
```

### 6.3 Verify replay works

Pick a known URL in the job (example HC homepage):

```bash
curl -I "https://replay.healtharchive.ca/job-1/https://www.canada.ca/en/health-canada.html" | head
```

In a browser:

- Open the same URL and click around.
- Confirm CSS/images load and links stay under `replay.healtharchive.ca/job-1/...`.

### 6.4 Repeat for another job (example: CIHR)

Once the CIHR legacy WARCs are imported and indexed as an `ArchiveJob` (see
`docs/operations/legacy-crawl-imports.md`), repeat the same steps with that job
ID:

- Recommended:
  - `ha-backend replay-index-job --id <id>`
- `wb-manager init job-<id>`
- Symlink that job’s WARCs into `/srv/healtharchive/replay/collections/job-<id>/archive/`
- `wb-manager reindex job-<id>`
- Verify: `https://replay.healtharchive.ca/job-<id>/<some captured url>/`

## 7) Troubleshooting

- **Blank pages / missing styling:** the asset was not captured into the WARC set, or the page uses live third‑party resources not archived.
- **Replay 404 but snapshot exists in DB:** the job’s WARCs were not linked+indexed into pywb (or you ran `cleanup-job` and deleted WARCs).
- **Replay UI shows “All-time (0 captures)”:** that exact URL (including scheme + host, eg `www.` vs non-`www`) likely isn’t present in the WARC set. Confirm via `/<collection>/cdx?url=...` and try host/scheme variants.
- **Iframe blocked:** check `frame-ancestors` header on `replay.healtharchive.ca` and ensure you removed `X-Frame-Options`.
- **Service crash-loop with `groupadd/useradd` and `su: Authentication failure`:** the container entrypoint is trying to create/switch users, but `--cap-drop=ALL` removes the capabilities needed. Fix by running the container as the host UID/GID directly via `--user <hareplay_uid>:<healtharchive_gid>`.

## 8) Backend wiring (optional, but recommended)

If you want the HealthArchive frontend to embed replay by default, configure the
backend to emit a `browseUrl` for each snapshot.

On the VPS (backend host), set in `/etc/healtharchive/backend.env`:

```bash
HEALTHARCHIVE_REPLAY_BASE_URL=https://replay.healtharchive.ca
```

Then restart the backend service.

### 8.1 Edition switching (v2: “preserve current page across backups”)

HealthArchive supports switching “editions” (jobs) while keeping you on the
same original URL when possible.

This is implemented as:

- `GET /api/sources/{sourceCode}/editions`
  - lists replayable jobs (editions) for the source, including each job’s
    `entryBrowseUrl` (a good fallback when a specific page wasn’t captured).
- `POST /api/replay/resolve`
  - input: `{ "jobId": <id>, "url": "<original_url>", "timestamp14": "YYYYMMDDhhmmss" | null }`
  - output: a best-effort `browseUrl` for the selected job if a capture exists
    (or `found=false` when it does not).

The frontend relies on lightweight `postMessage` events emitted by the replay
banner template (see “Optional: HealthArchive banner on direct replay pages”
above) to learn the **current original URL** while the user clicks around
inside replay.

Frontend-side details and verification are documented in:

- `healtharchive-frontend/docs/deployment/verification.md`

Frontend verification (recommended):

- See `healtharchive-frontend/docs/deployment/verification.md` for the end-to-end
  checks that confirm:
  - snapshot pages embed replay correctly, and
  - `/browse/<snapshotId>` provides a full-screen browsing wrapper with a
    persistent HealthArchive banner above the replay iframe.

## 9) Cached source preview images (optional, recommended)

The frontend `/archive` page can show a lightweight “homepage preview” tile for
each source’s latest replayable backup.

To avoid rendering live iframes on every page load, these previews are served as
cached static images generated out-of-band.

### 9.1 Configure preview directory (VPS)

Choose a directory on the VPS:

- Recommended: `/srv/healtharchive/replay/previews`

Create it with the same ownership model as the replay volume:

```bash
sudo mkdir -p /srv/healtharchive/replay/previews
sudo chown -R hareplay:healtharchive /srv/healtharchive/replay/previews
sudo chmod 2770 /srv/healtharchive/replay/previews
```

In `/etc/healtharchive/backend.env`, set:

```bash
HEALTHARCHIVE_REPLAY_PREVIEW_DIR=/srv/healtharchive/replay/previews
```

Then restart the API:

```bash
sudo systemctl restart healtharchive-api
```

### 9.2 Generate previews (VPS)

Generate (or refresh) previews for all sources with:

```bash
sudo systemd-run --wait --pipe \
  --property=EnvironmentFile=/etc/healtharchive/backend.env \
  /opt/healtharchive-backend/.venv/bin/ha-backend replay-generate-previews
```

This uses a Playwright container to screenshot each source’s `entryBrowseUrl`
(with `#ha_nobanner=1` so the pywb banner is not captured).

Note: The generator caches Playwright’s Node.js dependencies under
`<preview_dir_parent>/.preview-node/`. If you point `HEALTHARCHIVE_REPLAY_PREVIEW_DIR`
at a path inside your repo for local testing, ensure `.preview-node/` is ignored
by git (it is in `.gitignore`).

### 9.3 Verify previews are available

1) Confirm `/api/sources` advertises `entryPreviewUrl` where available:

```bash
curl -s https://api.healtharchive.ca/api/sources | python3 -m json.tool | rg entryPreviewUrl
```

2) Confirm an individual preview serves as an image:

```bash
curl -I "https://api.healtharchive.ca/api/sources/hc/preview?jobId=1" | head
```
