# Debugging a Failed Crawl

A practical tutorial for diagnosing and fixing common crawl failures.

**Scenario**: You created a crawl job, but it failed. Now what?

**Time**: 15-30 minutes
**Prerequisites**: Basic command line skills, access to the backend server

---

## Step 1: Identify the Failed Job

First, find the job ID and understand what went wrong.

### Check Job Status

```bash
ha-backend list-jobs
```

**Example output:**
```
ID  Name            Source  Status  Queued              Started             Finished            Pages
42  hc-20260118     hc      failed  2026-01-18 20:00:00 2026-01-18 20:05:00 2026-01-18 20:45:00 0
```

### Get Detailed Job Info

```bash
ha-backend show-job --id 42
```

**Look for these key fields:**
```json
{
  "id": 42,
  "name": "hc-20260118",
  "status": "failed",
  "crawler_exit_code": 1,
  "crawler_status": "failed",
  "pages_crawled": 147,
  "pages_total": 500,
  "pages_failed": 12,
  "output_dir": "/mnt/nasd/nobak/healtharchive/jobs/hc/20260118T200500Z__hc-20260118",
  "combined_log_path": "/mnt/.../archive_crawl_20260118T200511Z.combined.log",
  "retry_count": 0
}
```

**Key indicators:**
- `crawler_exit_code != 0` → Archive tool process failed
- `crawler_status = "failed"` → Crawl did not complete successfully
- `retry_count` → How many times we've already retried

---

## Step 2: Check the Crawl Logs

Logs are your best friend for debugging. Let's examine them systematically.

### Find the Log File

The `combined_log_path` from `show-job` tells you where to look:

```bash
JOB_ID=42
OUTPUT_DIR=$(ha-backend show-job --id $JOB_ID | jq -r '.outputDir')
LOG_PATH=$(ha-backend show-job --id $JOB_ID | jq -r '.combinedLogPath')

# View the log
less "$LOG_PATH"
```

### Common Log Patterns

#### 1. **Permission Denied**

```
ERROR: Permission denied: '/mnt/nasd/nobak/healtharchive/jobs/hc/...'
```

**Diagnosis**: Output directory has wrong permissions

**Fix**:
```bash
# Check permissions
ls -la "$(dirname "$OUTPUT_DIR")"

# Fix ownership (if needed)
sudo chown -R healtharchive:healtharchive "$OUTPUT_DIR"
sudo chmod -R 755 "$OUTPUT_DIR"
```

**Root cause**: Often happens after manual operations as root user

---

#### 2. **Docker Not Running**

```
ERROR: Cannot connect to the Docker daemon at unix:///var/run/docker.sock
```

**Diagnosis**: Docker service is down

**Fix**:
```bash
# Check Docker status
sudo systemctl status docker

# Start Docker if stopped
sudo systemctl start docker

# Verify Docker works
docker ps
```

**Prevention**: Enable Docker to start on boot:
```bash
sudo systemctl enable docker
```

---

#### 3. **Out of Disk Space**

```
ERROR: No space left on device
```

**Diagnosis**: Disk full

**Fix**:
```bash
# Check disk usage
df -h /mnt/nasd/nobak

# Find large directories
du -sh /mnt/nasd/nobak/healtharchive/jobs/* | sort -rh | head -10

# Clean up old jobs (carefully!)
ha-backend cleanup-job --id OLD_JOB_ID --mode temp

# Or manually remove old temp directories
rm -rf /mnt/nasd/nobak/healtharchive/jobs/hc/*/.tmp_*
```

**See**: `operations/playbooks/manage-warc-cleanup.md`

---

#### 4. **Network Timeout**

```
WARNING: Request timeout for https://www.canada.ca/...
ERROR: Max retries exceeded
```

**Diagnosis**: Network connectivity issues or slow responses

**Fix**:
```bash
# Test connectivity
curl -I https://www.canada.ca/en/health-canada.html

# Check DNS
dig www.canada.ca

# If VPN is enabled, check VPN status
tailscale status
```

**Workaround**: Increase timeouts in job config (see Step 5)

---

#### 5. **Crawl Stalled**

```
INFO: Pages crawled: 147/500 (29%)
... [no new log entries for 30+ minutes]
```

**Diagnosis**: Crawl made progress but stopped advancing

**Possible causes**:
- Site became unresponsive
- Workers all blocked on slow pages
- Memory issues in Docker container

**Fix**:
```bash
# Check if Docker container is still running
docker ps

# Check Docker logs
docker logs $(docker ps -q --filter ancestor=ghcr.io/openzim/zimit)

# Check system resources
htop

# If stalled, kill and retry
docker stop $(docker ps -q --filter ancestor=ghcr.io/openzim/zimit)
ha-backend retry-job --id 42
```

**See**: Real incident report: [operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md](../operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md)

---

#### 6. **Zimit Errors**

```
zimit: error: unrecognized arguments: --bad-flag
```

**Diagnosis**: Invalid passthrough arguments to zimit

**Fix**: Check job config:
```bash
ha-backend show-job --id 42 | jq '.config.zimit_passthrough_args'
```

**Remove invalid flags** and recreate job with correct config

**Reference**: See [zimit documentation](https://github.com/openzim/zimit) for valid flags

---

## Step 3: Inspect the Job Directory

Sometimes logs don't tell the full story. Let's check the filesystem.

### Navigate to Output Directory

```bash
cd "$OUTPUT_DIR"
ls -lah
```

**Expected structure:**
```
.archive_state.json           # Crawl state tracking
.tmp_1/                       # Temporary crawl artifacts
├── collections/
│   └── crawl-20260118.../
│       └── archive/
│           ├── rec-00000-20260118.warc.gz
│           └── ...
archive_crawl_....log         # Individual stage logs
archive_crawl_....combined.log # Aggregated log
zim/                          # ZIM output (if built)
```

### Check Crawl State

```bash
cat .archive_state.json | jq '.'
```

**Key fields:**
```json
{
  "run_mode": "Fresh",
  "temp_dir_counter": 1,
  "temp_dirs": [".tmp_1"],
  "current_stage": "crawl",
  "is_complete": false
}
```

**Indicators:**
- `is_complete: false` → Crawl didn't finish
- `current_stage` → What stage failed

### Check WARC Files

```bash
# Count WARC files
find .tmp_1 -name "*.warc.gz" | wc -l

# Check sizes
find .tmp_1 -name "*.warc.gz" -exec ls -lh {} \; | head -5

# Verify WARCs are readable
warcio check .tmp_1/collections/*/archive/rec-00000*.warc.gz
```

**Red flags:**
- 0 WARC files → Crawl never started
- Very small WARCs (< 1KB) → Likely corrupt
- WARC validation errors → Damaged files

---

## Step 4: Check System Resources

Resource exhaustion is a common cause of failures.

### Memory

```bash
# Current memory usage
free -h

# Memory usage during crawl (if still running)
docker stats
```

**Fix for memory issues:**
- Reduce `initial_workers` in job config
- Add swap space
- Upgrade server RAM

### CPU

```bash
# CPU load
uptime

# Top processes
htop
```

**Fix for high CPU:**
- Reduce worker count
- Check for competing processes
- Consider time-based scheduling

### Disk I/O

```bash
# Check I/O wait
iostat -x 1 5

# Disk usage
df -h

# Inode usage (can be exhausted even with space available)
df -i
```

---

## Step 5: Retry with Adjustments

Now that you've identified the issue, let's fix it and retry.

### Simple Retry (No Changes)

If the issue was transient (network blip, temporary resource exhaustion):

```bash
ha-backend retry-job --id 42
```

This sets `status = "retryable"` and the worker will pick it up.

### Retry with Modified Config

If you need to change job settings, create a new job with overrides:

```bash
ha-backend create-job --source hc \
  --override '{"tool_options": {"initial_workers": 2, "enable_monitoring": true, "stall_timeout_minutes": 60}}'
```

**Common overrides:**
- `initial_workers: 2` → More parallelism (or `1` if resource-constrained)
- `enable_monitoring: true` → Enable stall detection
- `stall_timeout_minutes: 60` → Abort if no progress for 60 mins
- `error_threshold_timeout: 50` → Tolerate more timeouts before adaptations
- `error_threshold_http: 50` → Tolerate more HTTP/network errors before adaptations
- `backoff_delay_minutes: 2` → Shorten post-adaptation sleep on single-worker hosts
- `page_limit: 1000` → Limit crawl scope for development/testing (avoid for annual campaign completeness)

### Resume Existing Crawl

Archive tool can resume from existing state:

```bash
# The output_dir still exists, so just retry
ha-backend retry-job --id 42
```

Archive tool will detect `.archive_state.json` and resume.

**Resume behavior** (see `src/archive_tool/docs/documentation.md`):
- `run_mode: "Resume"` if state indicates incomplete crawl
- Reuses existing WARCs
- Continues from last checkpoint

---

## Step 6: Verify the Fix

After retrying, monitor the job closely.

### Watch Job Progress

```bash
# Poll job status
watch -n 30 'ha-backend show-job --id 42 | jq ".status, .pagesCrawled, .pagesTotal"'
```

### Tail the Logs

```bash
tail -f "$LOG_PATH"
```

**Look for:**
- `INFO: Pages crawled: X/Y` → Progress increasing
- `INFO: Crawl stage completed successfully` → Success
- No new ERROR lines

### Check Metrics

If you have Prometheus metrics enabled:

```bash
curl -H "X-Admin-Token: $HEALTHARCHIVE_ADMIN_TOKEN" \
  https://api.healtharchive.ca/metrics | grep healtharchive_jobs
```

---

## Step 7: Post-Mortem (For Serious Failures)

If this was a significant failure (e.g., production annual crawl), document it.

### Create Incident Note

```bash
cp docs/_templates/incident-template.md \
   docs/operations/incidents/$(date +%Y-%m-%d)-brief-description.md
```

**Fill in**:
- Timeline of events
- Impact (pages missed, data loss)
- Root cause
- Resolution steps
- Preventive measures

**Example**: [operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md](../operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md)

### Update Runbooks

If you discovered a new failure mode or solution:

1. Update the relevant playbook or this tutorial
2. Add to troubleshooting FAQ
3. Submit a PR

---

## Common Failure Scenarios & Solutions

| Symptom | Likely Cause | Fix | Prevention |
|---------|--------------|-----|------------|
| `exit_code: 1`, "Permission denied" | Wrong file permissions | `chown`/`chmod` output dir | Use dedicated user, avoid sudo |
| `exit_code: 125`, "Docker not found" | Docker not running | `systemctl start docker` | Enable Docker on boot |
| "No space left" in logs | Disk full | Cleanup old jobs | Monitor disk usage, automate cleanup |
| Crawl stalled, no progress | Network issues or slow site | Enable monitoring, retry | Use `stall_timeout_minutes` |
| 0 WARCs created | Crawl failed immediately | Check seeds, Docker logs | Validate seeds before creating job |
| WARCs exist but index fails | WARC corruption | Re-crawl or skip corrupt files | Verify WARC integrity post-crawl |
| `retry_count: 3`, still failing | Persistent issue | Manual intervention needed | Review config, escalate |

---

## Debugging Checklist

Use this checklist for systematic debugging:

- [ ] Identify job ID and get detailed status (`show-job`)
- [ ] Read crawl logs (`combined_log_path`)
- [ ] Check for common error patterns (permissions, Docker, disk, network)
- [ ] Inspect job directory structure and files
- [ ] Verify WARC files exist and are valid
- [ ] Check system resources (memory, CPU, disk)
- [ ] Identify root cause
- [ ] Apply fix (permissions, config, cleanup)
- [ ] Retry job with adjustments
- [ ] Monitor retry for success
- [ ] Document incident if significant
- [ ] Update runbooks/playbooks with learnings

---

## Advanced Debugging

### Enable Debug Logging

Create job with verbose logging:

```bash
ha-backend create-job --source hc \
  --override '{"tool_options": {"log_level": "DEBUG"}}'
```

### Run Archive Tool Manually

For deep debugging, run archive-tool outside the backend:

```bash
archive-tool \
  --name debug-crawl \
  --output-dir /tmp/debug-crawl \
  --initial-workers 1 \
  --log-level DEBUG \
  https://www.canada.ca/en/health-canada.html
```

### Inspect Docker Container

If the container is still running:

```bash
# Get container ID
CONTAINER_ID=$(docker ps -q --filter ancestor=ghcr.io/openzim/zimit)

# Check logs
docker logs $CONTAINER_ID

# Exec into container
docker exec -it $CONTAINER_ID /bin/bash

# Inside container:
# - Check /output/
# - Review zimit logs
# - Inspect environment
```

### Check Database State

```bash
# Connect to database
sqlite3 healtharchive.db  # or psql for Postgres

# Check job status
SELECT id, name, status, crawler_exit_code, pages_crawled, pages_total
FROM archive_jobs
WHERE id = 42;

# Check if any snapshots were indexed
SELECT COUNT(*) FROM snapshots WHERE job_id = 42;
```

---

## Getting Help

If you're still stuck:

1. **Check existing incidents**: [operations/incidents/](../operations/incidents/README.md)
2. **Review playbooks**: [operations/playbooks/](../operations/playbooks/README.md)
3. **Search GitHub issues**: [github.com/jerdaw/healtharchive-backend/issues](https://github.com/jerdaw/healtharchive-backend/issues)
4. **Ask for help**: Open a new issue with:
   - Job ID and status output
   - Relevant log excerpts
   - Steps you've already tried
5. **Consult archive-tool docs**: `src/archive_tool/docs/documentation.md`

---

## Related Resources

- **Architecture Guide**: [architecture.md](../architecture.md)
- **Archive Tool Documentation**: `src/archive_tool/docs/documentation.md`
- **Incident Response**: [operations/playbooks/incident-response.md](../operations/playbooks/incident-response.md)

---

## Conclusion

Most crawl failures fall into a few categories:
- **Permissions**: Fix ownership and modes
- **Resources**: Free up disk, memory, or adjust worker count
- **Configuration**: Correct invalid options or seeds
- **Network**: Retry or adjust timeouts

With systematic debugging, you can identify and fix most issues quickly. Document significant failures so the next operator can benefit from your learnings!
