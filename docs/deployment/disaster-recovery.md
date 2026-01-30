# Disaster Recovery Runbook

**Last Updated:** 2026-01-18
**Status:** Active

## Recovery Objectives

In the context of HealthArchive, these objectives define our boundaries for data loss and downtime during a major failure.

- **RPO (Recovery Point Objective):** The maximum age of files that must be recovered from backup storage for operations to resume. It defines our "data loss tolerance."
- **RTO (Recovery Time Objective):** The maximum duration of time within which service must be restored after a disaster. It defines our "downtime tolerance."
- **MTTR (Mean Time To Recovery):** The average time taken to repair a failed component and return it to service.

### RPO (Recovery Point Objective)
**Target:** 24 hours

**Rationale:**
- We perform nightly backups of the database and configuration.
- Crawl data (WARCs) is tiered to storage regularly.
- Up to 24 hours of data loss (recent crawls, user actions) is considered acceptable for the current service criticality level (research access, no real-time critical operational dependencies). Data can often be re-crawled.

### RTO (Recovery Time Objective)
**Target:** 8 hours

**Rationale:**
- Recovery involves manual provisioning of a new VPS, installing dependencies, and restoring from backup.
- This timeframe allows a single operator to perform these steps during a standard workday.

### MTTR (Mean Time To Recovery)
**Target:** 4 hours

**Rationale:**
- For partial failures (e.g., service restart, database recovery without full VPS loss), we aim to restore service within 4 hours.

### When to Revisit
These targets should be reviewed:
- **Annually:** During the full DR drill.
- **Service Changes:** If the service criticality increases (e.g., adding real-time users).
- **Architecture Changes:** If moving from a single VPS to a multi-node/HA setup.
- **Scale Changes:** If the dataset size grows significantly enough to impact restoration times.

## Scenarios

### Scenario A: Complete VPS loss (NAS backup available)
Most likely DR scenario. Requires provisioning new VPS and restoring from offsite NAS backup.

### Scenario B: Database corruption
Restoration from local or NAS `pg_dump`.

### Scenario C: Storage failure
Recovery of WARC files from tiered storage or accepted data loss.

## Procedures

### 1. VPS Complete Restoration (Scenario A)

**Prerequisites:**
- Access to Hetzner Cloud Console.
- Access to Synology NAS (via physical access or alternative network if Tailscale is down).
- SSH key for `haadmin` available locally.

#### Step 1: Provision New VPS
1.  **Create Server:** Follow the standard provisioning steps in [Production Single VPS](production-single-vps.md#2-provision-os-hardening-hetzner).
    - Image: Ubuntu 24.04 LTS.
    - Updates: `sudo apt update && sudo apt upgrade -y`.
    - User: Create `haadmin` user and harden SSH.

2.  **Configure Networking:**
    - Set up Firewall rules (Allow 80/443, block 22 public, allow Tailscale UDP).

#### Step 2: Install Base Dependencies
Run as `haadmin`:
```bash
sudo apt install -y docker.io postgresql postgresql-contrib python3-venv python3-pip git curl build-essential pkg-config unzip
sudo systemctl enable --now docker postgresql
```

#### Step 3: Re-join Tailscale
1.  Install Tailscale: `curl -fsSL https://tailscale.com/install.sh | sh`.
2.  Authenticate: `sudo tailscale up --ssh`.
    - *Note:* If possible, reuse the old IP/hostname from the admin console to simplify ACLs, or update ACLs to trust the new node.

#### Step 4: Prepare Directories
```bash
sudo groupadd --system healtharchive
sudo mkdir -p /srv/healtharchive/{jobs,backups,ops}
sudo chown -R haadmin:haadmin /srv/healtharchive/jobs
sudo chown root:healtharchive /srv/healtharchive/backups /srv/healtharchive/ops
sudo chmod 2770 /srv/healtharchive/backups /srv/healtharchive/ops
```

#### Step 5: Retrieve Backup from NAS
If Tailscale is up on both ends:
1.  SSH to NAS: `ssh user@nas-ip`.
2.  Rsync backup to new VPS:
    ```bash
    rsync -av /volume1/nobak/healtharchive/backups/db/latest.dump haadmin@new-vps-ip:/srv/healtharchive/backups/
    ```
    *Alternatively, pull from VPS:*
    ```bash
    scp user@nas-ip:/path/to/backup.dump /srv/healtharchive/backups/latest.dump
    ```

#### Step 6: Restore Database
1.  Create DB and User:
    ```bash
    sudo -u postgres psql -c "CREATE USER healtharchive WITH PASSWORD '<password_from_backup_env>';"
    sudo -u postgres psql -c "CREATE DATABASE healtharchive OWNER healtharchive;"
    ```
2.  Restore Schema and Data:
    ```bash
    sudo -u postgres pg_restore -d healtharchive /srv/healtharchive/backups/latest.dump
    ```

#### Step 7: Restore Application
1.  Clone Repository:
    ```bash
    git clone https://github.com/jerdaw/healtharchive-backend.git /opt/healtharchive-backend
    cd /opt/healtharchive-backend
    python3 -m venv .venv
    ./.venv/bin/pip install -e ".[dev]" "psycopg[binary]"
    ```
2.  Restore Configuration:
    - Restore `/etc/healtharchive/backend.env` from your distinct secure offsite storage (e.g., password manager notes). **Do not lose this file.**
    - If needed, regenerate the `ADMIN_TOKEN`.

#### Step 8: Re-mount Storage / Restore WARCs
- Mount the Storage Box (tiered storage) to `/srv/healtharchive/storagebox` using `sshfs` (see `production-single-vps.md`).
- If local WARCs were lost (`/srv/healtharchive/jobs`), you have two options:
    1.  **Rescan:** If files exist on Storage Box, re-import headers (slow).
    2.  **Empty Start:** Start with empty local jobs; historical data remains on Storage Box/index.

### 2. Database Intact Restoration (Scenario B)

Use this procedure when the VPS is running but the database is corrupted or dropped.

**Prerequisites:**
- Backup file available (local or NAS).
- PostgreSQL service is running.

#### Step 1: Locate Backup
- **Format:** `pg_dump -Fc` (custom format, compressed).
- **Local:** `/srv/healtharchive/backups/`
    - Naming: `healtharchive_<timestamp>.dump`
    - Retention: 14 days.
- **NAS:** `/volume1/nobak/healtharchive/backups/db/` (needs retrieval)
    - Retention: Long-term/Permanent.

#### Step 2: Restore Database
*Warning: This will overwrite the current database state.*

1.  **Drop and Recreate:**
    ```bash
    sudo -u postgres dropdb --if-exists healtharchive_restored
    sudo -u postgres createdb healtharchive_restored
    ```

2.  **Restore from Dump:**
    ```bash
    # Replace <backup_file> with actual filename
    sudo -u postgres pg_restore -d healtharchive_restored -Fc /path/to/backup.dump
    ```

3.  **Verify Restoration:**
    Check that tables are populated:
    ```bash
    sudo -u postgres psql -d healtharchive_restored -c "SELECT count(*) FROM snapshots;"
    ```

4.  **Swap Databases:**
    Stop services to preventing locking:
    ```bash
    sudo systemctl stop healtharchive-api healtharchive-worker
    ```

    Swap:
    ```bash
    sudo -u postgres psql -c "ALTER DATABASE healtharchive RENAME TO healtharchive_old;"
    sudo -u postgres psql -c "ALTER DATABASE healtharchive_restored RENAME TO healtharchive;"
    ```

5.  **Restart Services:**
    ```bash
    sudo systemctl start healtharchive-api healtharchive-worker
    ```

#### Step 3: Integrity Verification
- **Row Counts:** Compare `SELECT count(*) FROM snapshots` with expected values.
- **Recent Data:** Check for the most recent captures `SELECT * FROM snapshots ORDER BY id DESC LIMIT 5;`.
- **Foreign Keys:** `pg_restore` would have failed on constraint violations, but check application logs for ORM errors.
- **Orphaned Records:** Ensure core relations are intact:
  ```sql
  -- Check for snapshots without sources
  SELECT count(*) FROM snapshots WHERE source_id NOT IN (SELECT id FROM sources);
  ```

#### Step 4: Partial Restoration (Advanced)
- **Specific Table:** Use `pg_restore -t <tablename>` to restore only one table to a temp DB, then copy data.
- **Verify on Separate Server:** For high-stakes restorations, perform the restoration on a development or temporary VPS first to verify integrity before swapping production.
- **Point-in-Time:** Requires WAL archiving (currently **not enabled**; rely on nightly dumps).

### 3. Archive Root Recovery (Scenario C)

Use this procedure when WARC files or the archive storage structure is compromised.

**Archive Root Structure:**
```bash
/srv/healtharchive/jobs/
├── <source_slug>-<year>-<month>/  # Job Output Directories
│   ├── warcs/                     # Stable WARC files
│   │   ├── manifest.json          # Mapping of source -> stable filenames
│   │   └── warc-000001.warc.gz
│   ├── provenance/                # Metadata preservation
│   │   └── archive_state.json
│   └── logs/
└── tiered/                        # Mount point for cold storage (Storage Box)
```

#### Recovery Scenarios

**Case 1: Local WARCs lost (e.g., accidental deletion), Tiered storage intact**
This is the most common recovery case.
1.  **Check Tiered Storage:** Verify header-only WARCs or full files exist in `/srv/healtharchive/storagebox`.
    2.  **Re-import Headers/WARCs (Slow but safe):**
        If the database is intact, you don't *need* the local WARCs effectively immediately for the site to work, but the Replay service will fail for those snapshots.
        To restore replayability, copy the WARCs back from tiered storage:
        ```bash
        # Example: Restore specific job
        rsync -av /srv/healtharchive/storagebox/jobs/hc-2026-01/ /srv/healtharchive/jobs/hc-2026-01/
        ```
    3.  **Verify against Manifest:**
        ```bash
        # Check that all files in manifest exist and have correct sizes
        cat /srv/healtharchive/jobs/hc-2026-01/warcs/manifest.json | jq .records
        ```

**Case 2: Tiered storage unavailable, Local intact**
1.  **Run in Degraded Mode:** Operations can continue using local WARCs.
2.  **Disable Tiering:** Stop the tiering cron job/timer to prevent errors.
3.  **Restore Connection:** Troubleshoot `sshfs` mount or Storage Box availability.
4.  **Re-enable Tiering:** Once fixed, the system will resume tiering new WARCs.

**Case 3: All copies lost (Catastrophic)**
1.  **Accept Data Loss:** Crawl data is gone.
2.  **Clean Database:** You may need to truncate `snapshots` table if it references missing files, or mark them as lost.
3.  **Re-crawl:** Trigger new manual crawls for critical sources.

#### Integrity Verification
1.  **WARC Validation:**
    ```bash
    # Validate a single WARC file
    warcio validate /path/to/file.warc.gz
    ```
2.  **Database Consistency:**
    Ensure database records point to existing files (custom script required).

#### Re-tiering and Consolidation Procedure
If tiered storage was wiped and replaced, or if you need to stabilize newly crawled data:

1.  **Consolidate WARCs:** Ensure files are moved from `.tmp*` to stable `warcs/` folders and manifests are updated.
    ```bash
    # Run as haadmin in .venv
    ha-backend consolidate-warcs --id <JOB_ID>
    ```
2.  **Verify Local Integrity:** Ensure local WARCs match their manifest and are valid.
3.  **Force Tiering:** Run the tiering command manually to re-upload everything:
    ```bash
    # Run as haadmin in .venv
    ha-backend tier-warcs --force --dry-run  # Check first
    ha-backend tier-warcs --force            # Execute
    ```
4.  **Verify Tiered Copies:** Check that the files on the Storage Box match the local stable WARCs.

### 4. Service Startup Sequence
Order is critical:

1.  **Database:** `sudo systemctl start postgresql`
    - **Health Check:** `sudo systemctl status postgresql` or `pg_isready`
    - **Failure:** Check disk space (`df -h`) and logs (`journalctl -u postgresql`).
2.  **API:** `sudo systemctl start healtharchive-api`
    - **Health Check:** `curl http://localhost:8001/api/health`
    - **Failure:** Check `/etc/healtharchive/backend.env` and `journalctl -u healtharchive-api -n 100`.
3.  **Worker:** `sudo systemctl start healtharchive-worker`
    - **Health Check:** `sudo systemctl status healtharchive-worker` (Check logs for "Worker started").
    - **Failure:** Check database connectivity and logs.
4.  **Replay (Optional):** Start pywb if configured.
    - **Health Check:** `curl http://localhost:8080` (or configured port).
5.  **Reverse Proxy:** `sudo systemctl start caddy`
    - **Health Check:** `sudo systemctl status caddy`
    - **Failure:** `sudo caddy validate --config /etc/caddy/Caddyfile`.

### 5. Verification Checklist
Run these checks immediately after startup:

- [ ] **Database Connectivity:** `sudo -u postgres psql -d healtharchive -c 'SELECT count(*) FROM sources;'` (Should > 0)
- [ ] **API Health:** `curl http://localhost:8001/api/health` -> `{"status":"ok"}`
- [ ] **Public Endpoint (HTTPS):** `curl -I https://api.healtharchive.ca/api/health` (Verify TLS works)
- [ ] **Search Index:** Query a known term via the frontend or API.
- [ ] **Worker Health:** Check logs for "Worker started" and no immediate crashes.
- [ ] **Snapshot Viewing:** Visit a known snapshot URL (e.g., the smoke test snapshot ID 1).
- [ ] **Monitoring Reconnected:** Confirm that Healthchecks.io, Prometheus, and external uptime monitors are receiving signals from the new VPS.

## DR Drills

Regular testing ensures that these procedures remain effective and that operators are familiar with the recovery process.

### Schedule

| Drill Type | Frequency | Next Due | Owner | Scope |
| :--- | :--- | :--- | :--- | :--- |
| **Tabletop** | Quarterly | Q1 2026 | Operator | Review procedure, check credentials, identify gaps. |
| **Partial Restore** | Quarterly | Q1 2026 | Operator | Restore database summary/integrity check on local dev machine. |
| **Full DR** | Annual | 2026-06 | Operator | Full recovery from backup to a fresh VPS. |

### Procedures

#### 1. Tabletop Drill
**Objective:** Verify documentation accuracy and credential availability without interacting with production.

1.  **Read-Through:** Walk through the "Complete VPS Restoration (Scenario A)" procedure step-by-step.
2.  **Credential Check:** Verify you can locate/access:
    - Hetzner Cloud Console password/2FA.
    - Synology NAS SSH keys.
    - Encrypted backup of `/etc/healtharchive/backend.env`.
    - Domain DNS controls (Namecheap).
3.  **Success Criteria:**
    - All restoration steps are understood and commands are valid.
    - All required credentials are confirmed as accessible and current.
4.  **Documentation & Follow-up:**
    - Fix any broken links, outdated commands, or unclear instructions found during the read-through.
    - Record findings in the **Results Log** (see below).

#### 2. Partial Restoration Drill
**Objective:** Verify backup integrity and database restorability.

1.  **Retrieve Backup:** Download the latest actual `healtharchive_<ts>.dump` from the NAS or VPS.
2.  **Local Restore:**
    - Spin up a local Docker Postgres container or use a local dev DB.
    - Run the **Scenario B (Database Corruption)** restoration steps against this local instance.
3.  **Success Criteria:**
    - `pg_restore` completes without fatal errors.
    - Row counts for `snapshots` match or are within expected growth margins.
    - Recent captures are present and readable.
4.  **Documentation & Follow-up:**
    - Record the size of the backup and restoration time in the **Results Log**.
    - If corruption is found, investigate backup job logs and schedule an immediate re-run.
5.  **Cleanup:** Delete the local test database and backup file.

#### 3. Full DR Drill (Annual)
**Objective:** Prove total system recovery capability.

**Prerequisites:**
- Perform during low-traffic window (e.g., weekend).
- Budget ~$5 for temporary VPS costs.

**Procedure:**
1.  **Provision:** Create a *new* VPS (e.g., `dr-test-2026`) in Hetzner. **DO NOT DELETE THE EXISTING PRODUCTION VPS.**
2.  **Execute Scenario A:** Follow "VPS Complete Restoration" strictly.
    - **Modification:** When restoring `backend.env`, change `HEALTHARCHIVE_PUBLIC_SITE_URL` to the temporary IP or a test subdomain to avoid DNS conflicts.
    - **Modification:** Do *not* switch the main DNS (A record) unless you are intentionally testing failover (requires downtime).
3.  **Verify & Success Criteria:**
    - Run the complete "Verification Checklist" on the new host; all checks must pass.
    - Verify you can pull a WARC file from tiered storage.
    - Total restoration time is within the **8-hour RTO**.
4.  **Documentation & Follow-up:**
    - Record total time to recovery (RTO metric) and any blockers in the **Results Log**.
    - Update the MTTR/RTO targets if they are consistently missed or easily exceeded.
5.  **Teardown:**
    - Destroy the temporary VPS.
    - Remove the temporary node from Tailscale.

### Results Log

Copy and paste this template to `docs/operations/dr-logs/<YYYY-MM-DD>-drill-report.md`:

```markdown
# DR Drill Report: <Date>

**Drill Type:** (Tabletop / Partial / Full)
**Operator:** <Name>
**Time Started:** <HH:MM UTC>
**Time Finished:** <HH:MM UTC>
**Total Duration:** <Minutes>

## Outcome
- [ ] Success (All objectives met)
- [ ] Partial Success (Objectives met with issues)
- [ ] Failure (Could not complete recovery)

## Metric
- **RTO Achieved:** N/A (or actual time if Full Drill)
- **Backup Age:** <Hours since last backup> (RPO check)

## Issues Encountered
1. Issue description...

## Documentation Updates Required
- [ ] Update section X.Y...
```
