# Incident: Annual crawl — job output dirs on root disk caused disk pressure + crawl pauses (2026-02-04)

Status: closed

## Metadata

- Date (UTC): 2026-02-04
- Severity (see `severity.md`): sev1
- Environment: production (single VPS)
- Primary area: storage
- Owner: (unassigned)
- Start (UTC): 2026-02-04T13:23:39Z (first operator snapshot showing disk pressure)
- End (UTC): 2026-02-04T16:47:12Z (operator snapshot showing jobs resumed + root disk healthy)

---

## Summary

The annual 2026 crawl campaign hit sustained root-disk pressure on the VPS (`/dev/sda1` reached ~84–86% used), which triggered the worker’s disk safety guardrail (≥85% usage) and prevented new crawl progress. Investigation showed that annual crawl output directories for CIHR (~50GB) and PHAC (~1.2GB) were on the **local root filesystem** under `/srv/healtharchive/jobs/**` instead of being tiered to the Storage Box. We paused crawls to avoid a disk-full failure, migrated the output directories to the Storage Box, re-established the expected mounts under `/srv/healtharchive/jobs/**`, and resumed automation.

## Impact

- User-facing impact:
  - Public API/site remained healthy.
  - Annual 2026 campaign was `Ready for search: NO` and made little/no crawl progress while paused.
- Internal impact (ops burden, automation failures, etc):
  - Operator time to pause long-running crawls and perform storage tiering.
  - Risk of a disk-full incident avoided by pausing early.
- Data impact:
  - Data loss: unknown (no evidence observed).
  - Data integrity risk: low-to-medium (risk was primarily “disk full” / interrupted writes if left running).
  - Recovery completeness: complete (output dirs remounted to Storage Box; jobs resumed).
- Duration:
  - Disk pressure was present before 2026-02-04T13:23:39Z and resolved by ~16:xxZ; campaign was running again by 16:47Z.

## Detection

- Operator ran `./scripts/vps-crawl-status.sh --year 2026` and saw:
  - Root disk at ~84–86% used.
  - Worker log warnings indicating the disk guardrail was active (“Disk usage at 85% exceeds threshold…”).
- `sudo du -xhd3 /srv/healtharchive/jobs | sort -h | tail` showed ~47–50GB under CIHR annual output on the local disk.

Most useful signals:

- `df -h /` (root usage)
- `sudo du -xhd3 /srv/healtharchive/jobs | sort -h | tail -40` (who is using local disk)
- `findmnt -T /srv/healtharchive/jobs/<source>/<job_dir> -o SOURCE,FSTYPE,OPTIONS` (is this actually on the Storage Box?)

## Decision log

- 2026-02-04T14:4xZ — Decision: pause all long-running annual crawls (why: avoid disk-full failure; preserve service and data integrity).
- 2026-02-04T15:3xZ — Decision: migrate annual output dirs with `rsync` (why: keep WARC/state artifacts; fastest path to reclaim root disk without losing crawl progress).
- 2026-02-04T16:0xZ — Decision: resume automation only after mounts validated (why: prevent immediately writing back to local disk).

## Timeline (UTC)

- 2026-02-04T13:23:39Z — `vps-crawl-status` snapshot: root disk ~86% used; annual campaign running (HC+CIHR), PHAC retryable.
- 2026-02-04T13:52:xxZ — Worker logs show disk guardrail active (≥85%), skipping crawl starts.
- 2026-02-04T14:4xZ — Automation disabled and crawls stopped; jobs recovered to `retryable` in DB for safe restart later.
- 2026-02-04T14:52:45Z — Fresh Postgres DB backup taken and copied to the Storage Box (with rsync flags adjusted to avoid sshfs `chown` failures).
- 2026-02-04T15:32:26Z — Storage Box `sshfs` mount confirmed active.
- 2026-02-04T15:3xZ → 16:0xZ — CIHR (~50GB) and PHAC (~1.2GB) annual output directories copied to the Storage Box via `rsync`.
- 2026-02-04T16:0xZ — Annual output tiering applied; output dirs mounted back under `/srv/healtharchive/jobs/**`; local copies deleted; root disk returned to ~19% used.
- 2026-02-04T16:15:33Z — Services restarted; worker resumed.
- 2026-02-04T16:47:12Z — `vps-crawl-status` snapshot shows 3 running annual jobs and root disk healthy.

## Root cause

- Immediate trigger:
  - Annual crawl output for at least CIHR and PHAC was written to the VPS root filesystem under `/srv/healtharchive/jobs/**`, consuming ~50GB locally and pushing root above the worker’s safety threshold.
- Underlying cause(s):
  - Annual output tiering/mounts were not in place for those job output dirs at the time the crawls ran (post-reboot / maintenance window).
  - Manual ops workflows are easy to run in an “unsafe order” (worker running while mounts not validated).
  - The annual output tiering script can be run with missing env exports / DB offline, which causes confusing failures (SQLite “no such table”) that can delay recovery.

## Contributing factors

- Long-running crawls reduce opportunities for a clean maintenance window.
- Root disk is fixed size and close to the worker’s disk threshold when any large output dir lands locally.
- `rsync` to `sshfs` mountpoints can fail on ownership/permissions by default (requires explicit flags).
- CIHR job (job 8) config drift: missing annual campaign metadata made annual tooling less reliable until patched.

## Resolution / Recovery

### 0) Pause/stop automation and crawls (make a maintenance window)

Disable the automations so they don’t immediately restart jobs while mounts are in flux:

```bash
# Disable crawl auto-recover and worker auto-start
sudo mv /etc/healtharchive/crawl-auto-recover-enabled{,.disabled} 2>/dev/null || true
sudo mv /etc/healtharchive/worker-auto-start-enabled{,.disabled} 2>/dev/null || true

sudo systemctl stop healtharchive-crawl-auto-recover.timer || true
sudo systemctl stop healtharchive-worker-auto-start.timer || true

# Stop worker (and any transient crawl units)
sudo systemctl stop healtharchive-worker.service || true
systemctl list-units --all 'healtharchive-job*' --no-pager
sudo systemctl stop <healtharchive-jobX-...>.service
```

Mark stopped jobs restartable:

```bash
set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 1 --apply --source <source>
```

### 1) Ensure backups exist

```bash
sudo systemctl start healtharchive-db-backup.service
ls -lt /srv/healtharchive/backups/healtharchive_*.dump | head -n 3
```

### 2) Restore/verify Storage Box mount

```bash
sudo systemctl start healtharchive-storagebox-sshfs.service
df -h /srv/healtharchive/storagebox
findmnt -T /srv/healtharchive/storagebox -o SOURCE,FSTYPE,OPTIONS
```

### 3) Migrate large local output dirs to Storage Box

Use `rsync` flags that don’t try to preserve ownership/perms on sshfs:

```bash
sudo rsync -rtv --info=progress2 --partial --inplace \
  --no-owner --no-group --no-perms \
  /srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101/ \
  /srv/healtharchive/storagebox/jobs/cihr/20260101T000502Z__cihr-20260101/
```

Optional “sanity dry-run” to see drift (but do not delete without thinking):

```bash
sudo rsync -rtvn --delete \
  --no-owner --no-group --no-perms \
  /srv/healtharchive/jobs/<source>/<job_dir>/ \
  /srv/healtharchive/storagebox/jobs/<source>/<job_dir>/
```

### 4) Re-establish the expected mounts under `/srv/healtharchive/jobs/**`

Key gotcha: the tiering script must target Postgres. Make sure env vars are exported and Postgres is running, otherwise you may see SQLite errors like `no such table: sources`.

```bash
sudo systemctl start postgresql.service
sudo bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; \
  /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py --apply --year 2026'
```

Validate mountpoints:

```bash
findmnt -T /srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101 -o SOURCE,FSTYPE
findmnt -T /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101 -o SOURCE,FSTYPE
findmnt -T /srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101 -o SOURCE,FSTYPE
```

### 5) Delete local copies and verify disk health

```bash
sudo rm -rf /srv/healtharchive/jobs/*/*__*.local-*
df -h /
sudo du -xhd3 /srv/healtharchive/jobs | sort -h | tail -40
```

### 6) Resume services and automation

```bash
# Re-enable sentinels
sudo mv /etc/healtharchive/crawl-auto-recover-enabled.disabled /etc/healtharchive/crawl-auto-recover-enabled 2>/dev/null || sudo touch /etc/healtharchive/crawl-auto-recover-enabled
sudo mv /etc/healtharchive/worker-auto-start-enabled.disabled /etc/healtharchive/worker-auto-start-enabled 2>/dev/null || sudo touch /etc/healtharchive/worker-auto-start-enabled

sudo systemctl enable --now healtharchive-crawl-auto-recover.timer
sudo systemctl enable --now healtharchive-worker-auto-start.timer

sudo systemctl start healtharchive-api.service healtharchive-replay.service postgresql.service
sudo systemctl start healtharchive-worker.service
```

## Post-incident verification

- Public surface checks:
  - `curl -s http://127.0.0.1:8001/api/health && echo`
  - `cd /opt/healtharchive-backend && ./scripts/verify_public_surface.py` (when appropriate)
- Worker/job health checks:
  - `cd /opt/healtharchive-backend && ./scripts/vps-crawl-status.sh --year 2026`
  - `systemctl list-units --all 'healtharchive-job*' --no-pager`
- Storage/mount checks:
  - `df -h / /srv/healtharchive/storagebox`
  - `findmnt -T /srv/healtharchive/jobs/<source>/<job_dir> -o SOURCE,FSTYPE,OPTIONS`

## Public communication

None. (No observed user-facing downtime; annual campaign internal pipeline issue.)

## Open questions

- What is the “source of truth” workflow after reboot/rescue to ensure annual output tiering is restored before the worker runs?
- Should we add a boot-time (or worker-start-time) invariant check that annual output dirs are mounted to the Storage Box?
- Can we reduce the likelihood of needing a rescue-mode window for “disk mystery” investigations by improving on-host diagnostics and documentation?

## Action items (TODOs)

- [ ] Add a runbook section: “Annual output tiering after reboot/rescue” (owner=ops, priority=high, due=2026-02-08)
- [ ] Add a guardrail: worker refuses to start annual crawls when output dir is on `/dev/sda1` (owner=eng, priority=high, due=2026-02-15)
- [ ] Improve `vps-annual-output-tiering.py` UX:
  - detect “Postgres not running / env not exported” and print a single-line fix. (owner=eng, priority=medium, due=2026-02-15)
- [ ] Ensure annual job configs always include `campaign_kind/year` metadata (owner=eng, priority=medium, due=2026-02-15)

## Automation opportunities

- Safe automation:
  - On boot (or before starting the worker), run an idempotent annual tiering “ensure” pass for the current campaign year.
  - Alert when `/srv/healtharchive/jobs/**` output dirs are on the root filesystem while a campaign is active.
- What should stay manual:
  - Any automated deletion of local `.local-*` directories should remain manual unless preceded by a strong integrity check (to avoid data loss).

## References / Artifacts

- Related investigation: `../../planning/implemented/2026-02-01-disk-usage-investigation.md`
- Related playbooks:
  - `../playbooks/storage/warc-storage-tiering.md`
  - `../playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
- Commands used during recovery:
  - `./scripts/vps-crawl-status.sh --year 2026`
  - `scripts/vps-annual-output-tiering.py`
  - `healtharchive-storagebox-sshfs.service`
