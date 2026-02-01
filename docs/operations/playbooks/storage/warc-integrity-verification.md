# WARC integrity verification (post-incident + pre-index)

Use this playbook when you suspect WARC corruption or replay integrity risk, especially after:

- sshfs/FUSE mount instability (`Errno 107: Transport endpoint is not connected`)
- unexpected crawler/container termination during WARC writes
- manual intervention on job output directories

This playbook is intentionally procedural; for background see:

- Roadmap/incident context: `docs/planning/implemented/2026-01-08-storagebox-sshfs-stale-mount-recovery-and-integrity.md`
- Storage infra recovery: `storagebox-sshfs-stale-mount-recovery.md`

## 0) Safety rules (do not skip)

- **Never quarantine while a job is `running`.**
- **Never quarantine after a job has been indexed** (i.e., when `Snapshot` rows exist): moving WARCs breaks replay because `Snapshot.warc_path` must remain valid.
- If verification failures are `infra_error`, treat it as a storage incident first (recover mounts), not corruption.

The CLI enforces the most important guards and will refuse unsafe operations.

## 1) Pick a verification level (cost vs confidence)

The `ha-backend verify-warcs` command supports three levels:

- **Level 0 (cheap)**: file exists, is readable, size > 0
- **Level 1 (moderate, default)**: gzip stream integrity (detect truncation/CRC issues)
- **Level 2 (heavier)**: WARC parseability (iterate records; streams bodies)

Recommended posture on a single VPS:

- Post-incident window: **Level 1** for WARCs touched during the incident window.
- “Always on” before indexing: **Level 0** (built into the indexing pipeline; optional deeper checks via env).

## 2) Verify WARCs for a job (report-only)

Run a report-only verification:

```bash
cd /opt/healtharchive-backend
sudo bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; ha-backend verify-warcs --job-id <JOB_ID> --level 1'
```

Bound the work if you’re validating an incident window:

```bash
ha-backend verify-warcs --job-id <JOB_ID> --level 1 --since-minutes 180 --limit-warcs 50
```

Optional: write a Prometheus node_exporter textfile metric:

```bash
ha-backend verify-warcs --job-id <JOB_ID> --level 1 --metrics-file /var/lib/node_exporter/textfile_collector/healtharchive_warc_verify.prom
```

## 3) If verification fails with `infra_error`

This is usually mount instability, not corruption.

- Follow `storagebox-sshfs-stale-mount-recovery.md`.
- After recovery, re-run `verify-warcs`.

## 4) If verification fails with `corrupt_or_unreadable` (pre-index only)

If the job has **no Snapshot rows** (not indexed), quarantine the corrupt WARCs:

```bash
ha-backend verify-warcs --job-id <JOB_ID> --level 1 --apply-quarantine
```

This will:

- move corrupt WARCs under `<output_dir>/warcs_quarantine/<timestamp>/...`
- write `<output_dir>/WARCS_QUARANTINED.txt` with provenance + sha256
- set the job back to `retryable` and reset `retry_count` so the worker can re-run it

Then let the worker pick it up (or restart the worker if it’s not running).

## 5) If verification fails after indexing (snapshots exist)

Do **not** quarantine: this breaks replay.

Treat it as a critical integrity incident:

- stop automated cleanup for the affected job
- preserve the job output directory as-is
- capture a verification report (`--json-out` recommended)
- decide whether to rebuild the dataset / replay from backups, or to re-crawl the affected source

Escalate via `incident-response.md` and record the outcome in `docs/operations/mentions-log.md`.
