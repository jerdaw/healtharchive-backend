# Replay service playbook (operators)

Goal: keep replay (`replay.healtharchive.ca`) available when the project relies on it.

Canonical references:

- Replay runbook: `../../deployment/replay-service-pywb.md`
- Production runbook: `../../deployment/production-single-vps.md`
- Replay automation design: `../replay-and-preview-automation-plan.md`

## Setup / recovery (if replay is missing)

Follow `../../deployment/replay-service-pywb.md`.

## Verify replay is working

1. Check the base URL is up:
   - `curl -I https://replay.healtharchive.ca/ | head`
2. Verify the public surface script can resolve a replay `browseUrl` for a known snapshot:
   - `cd /opt/healtharchive-backend && ./scripts/verify_public_surface.py`
3. Verify the replay banner works on a direct replay page:
   - Open a known `browseUrl` on `https://replay.healtharchive.ca/` and confirm the banner loads quickly and shows `title · snapshot date · original URL`, and that the action links (Details, View diff, Raw HTML, Metadata JSON, Cite, Report issue, Other snapshots) behave as expected.

## Retention warning

Replay depends on WARCs staying on disk. Do not delete WARCs for jobs you expect to replay.

## What “done” means

- `https://replay.healtharchive.ca/` responds successfully.
- `./scripts/verify_public_surface.py` reports a working replay `browseUrl` where expected.
