# 2026-01-19: Ops-First Monitoring Strategy (Textfile Collectors)

## Context

The annual crawl campaign (2026) required deep visibility into the crawling process (job 6, 7, etc.), which runs as a Docker container managed by `healtharchive-worker`. We needed to know:

1. Is the crawl actually writing pages? (Progress monitoring)
2. Is the SSHFS mount stable? (Infrastructure health)
3. Is independent indexing starting after the crawl finishes? (Pipeline integrity)

Existing Prometheus exporters (`node_exporter`) give system-level metrics but lack application-specific context for these batch jobs.

## Decision

We decided to implement an **"Ops-First" monitoring strategy** using the Prometheus Node Exporter Textfile Collector pattern, driven by simple Systemd timers and Python scripts.

### Key Components

1. **Script-Driven Metrics**: A dedicated script (`scripts/vps-crawl-metrics-textfile.py`) that queries the DB and probes filesystem state (logs, mounts) to generate `.prom` files.
2. **Systemd Timers**: Instead of a long-running daemon, we use `healtharchive-crawl-metrics-textfile.timer` to run the script every minute. This avoids memory leaks and makes the monitoring itself robust and stateless.
3. **State-File Coupling**: The crawler (`archive_tool`) writes a `.archive_state.json` file. The monitoring script consumes this. This decoupling means the monitor doesn't need to query the Docker container directly.

## Consequences

### Positive

- **Simplicity**: No new long-running services to manage.
- **Robustness**: If the monitor crashes, systemd restarts it next minute.
- **Decoupling**: Monitoring logic is separate from core crawler logic.

### Negative

- **Latency**: Metrics are updated minutely, not real-time (acceptable for long-running crawls).
- **Disk I/O**: Constant reading of logs/state files (mitigated by `tail` logic).

## Status

Accepted and Implemented (Phase 4 & 6 of Hardening Mission).
