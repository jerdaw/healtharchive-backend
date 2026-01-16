# Incident: Replay smoke tests failed (503) due to stale mounts + warc-tiering service failed (2026-01-16)

Status: closed

## Metadata

- Date (UTC): 2026-01-16
- Severity (see `severity.md`): sev1
- Environment: production
- Primary area: replay + storage
- Owner: (unassigned)
- Start (UTC): 2026-01-15T04:20:00Z (first observed failing replay-smoke metrics)
- End (UTC): 2026-01-16T02:51:56Z (replay-smoke metrics OK)

---

## Summary

The daily replay smoke tests began returning `503` for the legacy imported jobs (HC + CIHR), even though `https://replay.healtharchive.ca/` itself was up (`200`). The underlying issue was that the replay container could not reliably read WARCs under `/srv/healtharchive/jobs/imports/**` due to stale mountpoints (`Transport endpoint is not connected`) and the replay container’s mount namespace not reflecting repaired/updated mounts. Separately, `healtharchive-warc-tiering.service` had been left in a `failed` state since 2026-01-08, preventing tiered imports from being reliably mounted.

Recovery: re-apply WARC tiering, clear the failed systemd state, and restart the replay service to refresh its mounts; then re-run replay smoke tests.

## Impact

- User-facing impact: replay for legacy jobs intermittently failed (HTTP 503 responses from pywb for snapshot requests).
- Internal impact: `ReplaySmokeFailed` monitoring noise and operator intervention required.
- Data impact:
  - Data loss: no evidence
  - Data integrity risk: low/unknown (symptom was read failures, not WARC corruption)
  - Recovery completeness: complete (smoke tests returned `200`)
- Duration: ~22h (first failing metric to confirmed recovery)

## Detection

- node_exporter metrics:
  - `healtharchive_replay_smoke_ok{job_id="1",source="hc"} 0` + `status_code ... 503`
  - `healtharchive_replay_smoke_ok{job_id="2",source="cihr"} 0` + `status_code ... 503`
- systemd state:
  - `healtharchive-warc-tiering.service` was `failed` since 2026-01-08 with `Transport endpoint is not connected`.
- Container symptom:
  - `docker exec healtharchive-replay ... ls -la /warcs/imports/...` showed `d?????????` and `Transport endpoint is not connected`.

## Timeline (UTC)

- 2026-01-08T06:25:23Z — `healtharchive-warc-tiering.service` failed while attempting to operate on `/srv/healtharchive/jobs/imports/...` (stale mount: `Transport endpoint is not connected`).
- 2026-01-15T04:20:00Z — Replay smoke test metrics show `503` for legacy jobs (first observed failing `healtharchive_replay_smoke_*` timestamp).
- 2026-01-16T02:25Z — Verified replay root is up (`curl -I https://replay.healtharchive.ca/` returns `200`), but snapshot requests return `503`.
- 2026-01-16T02:30Z — Confirmed the replay container cannot read tiered import directories (`docker exec healtharchive-replay ...` shows `Transport endpoint is not connected`).
- 2026-01-16T02:51Z — Recovered by re-applying tiering + restarting replay:
  - `sudo systemctl reset-failed healtharchive-warc-tiering.service`
  - `sudo systemctl start healtharchive-warc-tiering.service`
  - `sudo systemctl restart healtharchive-replay.service`
  - `sudo systemctl start healtharchive-replay-smoke.service`
- 2026-01-16T02:51:56Z — Replay smoke metrics return to `200`:
  - `healtharchive_replay_smoke_ok{job_id="1",source="hc"} 1`
  - `healtharchive_replay_smoke_ok{job_id="2",source="cihr"} 1`

## Root cause

- Immediate trigger: one or more tiered paths under `/srv/healtharchive/jobs/imports/**` were stale/unreadable (`Errno 107: Transport endpoint is not connected`), causing WARC reads inside pywb to fail.
- Underlying cause(s):
  - `healtharchive-warc-tiering.service` remained `failed` after a prior storage incident, so tiered import mountpoints were not being applied/validated by systemd.
  - The replay service is a long-running Docker container bind-mounting `/srv/healtharchive/jobs` into `/warcs`. Mount changes/repairs on the host can require a container restart for the container to observe a clean view of the mountpoints.

## Contributing factors

- Tiered import jobs are critical to replay smoke (legacy jobs are used as smoke targets).
- Stale mount symptoms were partly masked because:
  - the Storage Box base mount looked healthy, and
  - replay root `/` still returned `200`.

## Resolution / Recovery

1) Ensure WARC tiering mounts are applied and systemd is not stuck in a failed state:

```bash
sudo systemctl reset-failed healtharchive-warc-tiering.service
sudo systemctl start healtharchive-warc-tiering.service
sudo systemctl status healtharchive-warc-tiering.service --no-pager -l
```

2) Restart replay so the container sees a clean view of `/srv/healtharchive/jobs`:

```bash
sudo systemctl restart healtharchive-replay.service
sudo systemctl status healtharchive-replay.service --no-pager -l
```

3) Re-run replay smoke and verify metrics:

```bash
sudo systemctl start healtharchive-replay-smoke.service
curl -s http://127.0.0.1:9100/metrics | rg '^healtharchive_replay_smoke_'
```

## Post-incident verification

- Public surface checks:
  - `curl -I https://replay.healtharchive.ca/ | head` returns `200`.
- Storage/mount checks:
  - `systemctl status healtharchive-warc-tiering.service --no-pager -l` is successful.
- Replay job checks:
  - `healtharchive_replay_smoke_ok{job_id="1",source="hc"} 1` and `...{job_id="2",source="cihr"} 1`

## Action items (TODOs)

- [ ] Update playbooks to call out “restart replay after mount/tiering repairs” when smoke returns `503` but replay root is `200`. (owner=, priority=high, due=)
- [ ] Consider enabling the storage hot-path auto-recover watchdog (`healtharchive-storage-hotpath-auto-recover.timer`) after validating thresholds. (owner=, priority=medium, due=)
- [ ] Add a healthcheck that alerts when `healtharchive-warc-tiering.service` is in a failed state for >N hours. (owner=, priority=medium, due=)

## References / Artifacts

- Tiering manifest (VPS): `/etc/healtharchive/warc-tiering.binds`
- Tiering script (VPS): `scripts/vps-warc-tiering-bind-mounts.sh`
- Replay smoke playbook: `../playbooks/replay-smoke-tests.md`
- Storage recovery playbook: `../playbooks/storagebox-sshfs-stale-mount-recovery.md`

