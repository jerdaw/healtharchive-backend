# Coverage guardrails (annual regression checks)

Goal: detect large year-over-year coverage drops after annual jobs are indexed.

Canonical refs:

- systemd unit templates: `../../deployment/systemd/README.md`
- monitoring checklist: `../monitoring-and-ci-checklist.md`

## What this does

- Compares the latest indexed **annual** job for each source to the prior year.
- Emits node_exporter textfile metrics:
  - `healtharchive_coverage_ratio{source="hc",year="2026"}`
  - `healtharchive_coverage_regression{source="hc",year="2026"}`
  - `healtharchive_coverage_warning{source="hc",year="2026"}`

## Enablement (VPS)

```bash
sudo touch /etc/healtharchive/coverage-guardrails-enabled
sudo systemctl enable --now healtharchive-coverage-guardrails.timer
```

## Manual check

```bash
sudo systemctl start healtharchive-coverage-guardrails.service
sudo journalctl -u healtharchive-coverage-guardrails.service -n 200 --no-pager
curl -s http://127.0.0.1:9100/metrics | rg '^healtharchive_coverage_'
```

## If an alert fires

1. Identify the affected source and year from the metric labels.
2. Confirm current and prior annual jobs:
   ```bash
   set -a; source /etc/healtharchive/backend.env; set +a
   /opt/healtharchive-backend/.venv/bin/ha-backend list-jobs --source hc --status indexed --limit 10
   /opt/healtharchive-backend/.venv/bin/ha-backend show-job --id <JOB_ID>
   ```
3. If the drop is real, inspect crawl logs for stalls/timeouts and consider:
   - re-running the crawl (retryable),
   - adjusting scope rules for that source,
   - or filing a follow-up for annual tuning.

## Config

Edit `ops/automation/coverage-guardrails.toml` to change thresholds.
