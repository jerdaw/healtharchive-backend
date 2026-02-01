# Disk Baseline and Automated Cleanup

**Last Updated**: 2026-02-01
**VPS**: Hetzner 75GB single-VPS production

## Current Baseline

**Normal operating disk usage**: ~82%
**Available space**: ~14GB
**Alert thresholds**:
- Warning: >85% for 30m
- Critical: >92% for 10m

## Why 82% Baseline?

The VPS uses a **tiered storage architecture**:
- **Local disk (75GB)**: System, Docker, logs, temp crawl data
- **Storagebox (1TB)**: Final WARCs, ZIMs, large job data via SSHFS mounts

Local disk breakdown (~61GB used):
- System/packages: ~3.1GB (`/usr`)
- Docker: ~7GB (`/var/lib/docker`)
- Logs: ~2GB (`/var/log`)
- Ephemeral data: ~1GB (`/srv` local, temp crawl dirs)
- OS/kernel: ~48GB (includes filesystem metadata, journal, reserves)

## Automated Cleanup

### 1. Docker Cleanup (Weekly)

**Timer**: `docker-cleanup.timer` (weekly)
**Script**: `/usr/local/bin/docker-cleanup.sh`
**Actions**:
```bash
docker image prune -a -f  # Remove unused images
docker system prune -f    # Remove stopped containers, networks
```

**Expected impact**: Frees 2-4GB per week

### 2. Log Rotation

**Journald** (`/etc/systemd/journald.conf`):
- `SystemMaxUse=500M` - Cap journal size
- `SystemKeepFree=2G` - Ensure 2GB always free
- `MaxFileSec=1week` - Rotate weekly

**Docker container logs** (`/etc/docker/daemon.json`):
- `max-size: 10m` - Max 10MB per log file
- `max-file: 3` - Keep 3 rotations (30MB total per container)

**Expected impact**: Prevents runaway log growth, keeps logs <2GB

### 3. Manual Cleanup Commands

When disk >85%, run these manually:

```bash
# Clean Docker
docker image prune -a -f
docker system prune -f

# Rotate logs
sudo journalctl --vacuum-size=500M

# Truncate large container logs
sudo truncate -s 0 /var/lib/docker/containers/*/CONTAINER-json.log

# Check what's consuming space
sudo du -xsh /* 2>/dev/null | sort -hr | head -10
```

## Worker Pre-Crawl Disk Check

**Threshold**: 85%
**Behavior**: Worker skips job selection if disk >85%

This prevents starting crawls that would fail mid-flight due to disk pressure.

## Monitoring

**Metrics**: `node_filesystem_avail_bytes`, `node_filesystem_size_bytes`
**Dashboard**: Grafana "HealthArchive - Infrastructure"
**Status command**: `ha-backend status` (shows disk usage with color coding)

## Troubleshooting

### Disk >85% Sustained

1. Check Docker images: `docker system df`
2. Check logs: `sudo du -sh /var/log`
3. Check temp crawl dirs: `du -xsh /srv/healtharchive/jobs/*/`
4. Run manual cleanup (see above)

### Disk >92% (Critical)

1. **Stop active crawls** if necessary: `docker ps` → `docker stop <id>`
2. Run all cleanup commands
3. Consider truncating container logs
4. If still critical, investigate filesystem accounting with `sudo du -xsh /`

### False Alarm: du Reports >100GB

If `du -sh /srv/healtharchive/jobs/*` reports huge sizes (>100GB), it's traversing SSHFS mounts and reporting remote storagebox data.

**Fix**: Use `du -xsh` to stay on local filesystem only:
```bash
sudo du -xsh /srv/healtharchive/jobs/*
```

Or just use `df -h /` for filesystem truth.

## History

- **2026-02-01**: Established 82% baseline after Docker/log cleanup freed 5.4GB
- **2026-01-31**: Disk pressure incident (89% → cleanup → 82%)
- **2026-01-24**: Automated tiering for annual jobs deployed
