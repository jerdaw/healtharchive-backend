# Automation Implementation Plan (Production-Only, Single VPS)

Status: **active plan** (implementation proceeds in phases).

This document is the **excruciatingly detailed**, sequential implementation
plan for HealthArchive automation, tailored to the current operating reality:

- **One production VPS** (no staging backend).
- Annual capture campaign runs on **Jan 01 (UTC)**.
- Current annual sources: **Health Canada (`hc`)**, **PHAC (`phac`)**, **CIHR (`cihr`)**.
- **No page/depth caps** (completeness/accuracy first).
- Top priority is making the annual snapshot **searchable ASAP** once crawls
  complete; replay/previews are secondary and eventually consistent.

This plan intentionally minimizes operational complexity:

- Every new automation starts as **manual + dry-run**, then graduates to a
  **systemd timer** only once it is boring and predictable.
- Every automated action must be:
  - idempotent,
  - allowlistable,
  - rate-limited,
  - observable,
  - and instantly disable-able.

Canonical “what we crawl”:

- `annual-campaign.md`

Related context:

- Monitoring/CI guidance: `monitoring-and-ci-checklist.md`
- Replay/preview automation design: `replay-and-preview-automation-plan.md`
- Production runbook: `../deployment/production-single-vps.md`

---

## Global invariants (do not violate)

### Safety

- **Never run heavy automation on request paths.** No crawl, indexing, replay
  indexing, or screenshotting should be triggered by a public HTTP request.
- **Never automate destructive cleanup** of WARCs until retention is designed
  and tested (see `replay-and-preview-automation-plan.md`).
- **No secrets in repo or logs.** Timers/services must read secrets from
  root-owned env files on the VPS, and logs must not print their contents.

### Idempotency and boundedness

- Every scheduled unit must be safe to run multiple times (systemd timers with
  `Persistent=true` can replay missed runs).
- Every automated loop must have:
  - hard caps (jobs per run, previews per run),
  - a lock (global and/or per-item),
  - and clear refusal rules (disk low, dependency down).

### “Single VPS” discipline

- Treat the worker as a scarce resource. Avoid adding competing heavy
  automation during the annual campaign.
- Prefer “queue work then let the worker run” over spawning extra parallel
  processes.

---

## Step 1 (of 9) — Define annual scope + seeds (docs only)

**Objective**

Lock the annual campaign’s scope so automation is deterministic and auditable.

**Deliverables**

- `annual-campaign.md`:
  - sources list (`hc`, `phac`, `cihr`),
  - canonical seeds (EN+FR where applicable),
  - scope boundary notes,
  - recommended crawl ordering.

**No code and no infrastructure changes** in this step.

**Acceptance criteria**

- Operators can answer “what will Jan 01 crawl?” by pointing at a single file.

**Rollback**

- N/A (docs only).

---

## Step 2 (of 9) — Align backend registry + seeding with v1 sources (code)

**Objective**

Ensure the backend’s canonical configuration matches the annual campaign:

- `seed-sources` creates `hc`, `phac`, `cihr`.
- Job registry can create annual jobs for these sources consistently.

**Key decisions**

- Registry remains the single source of truth for per-source defaults.
- We do **not** add page/depth limits to achieve “fast campaigns”; completeness
  remains the priority.
- `hc` and `phac` are **sections of `www.canada.ca`**, so their job configs must
  enforce a **path allowlist scope** (as defined in `annual-campaign.md`) to
  avoid crawling all of Canada.ca.

**Implementation steps**

1. Add/confirm `cihr` in source seeding (`ha-backend seed-sources`).
2. Add a `SourceJobConfig` entry for `cihr` in `job_registry.py`:
   - seeds from `annual-campaign.md`
   - conservative safety defaults (monitoring off by default unless you choose
     otherwise)
3. Update `hc` and `phac` seeds to match the canonical list (likely add FR
   entry points if not already).
4. Encode the `annual-campaign.md` in-scope URL rules into job configs:
   - for `hc` and `phac`: host + path allowlist scope on `www.canada.ca`
   - for `cihr`: host scope on `cihr-irsc.gc.ca`
5. Ensure naming templates can represent annual campaigns (see Step 3).

**Tests**

- Unit tests:
  - seeding includes `cihr`,
  - job creation for each source works,
  - config JSON contains expected seeds and scope constraints.

**Acceptance criteria**

- Running `seed-sources` on a fresh DB yields all three sources.
- `create-job --source cihr` works locally and yields a job row with expected
  defaults.

**Rollback**

- Revert code changes; no DB migrations required if only seeding/registry
  changes are made.

---

## Step 3 (of 9) — Implement annual scheduler CLI (production-only logic, dry-run first)

**Objective**

Provide a single, safe command that enqueues the annual campaign jobs for a
given year (Jan 01 UTC), exactly once.

**Proposed CLI**

- `ha-backend schedule-annual`

Flags (opinionated):

- `--apply` (otherwise dry-run only)
- `--year YYYY`
  - If omitted: allowed **only** when running on **Jan 01 (UTC)**, in which
    case it schedules the current UTC year.
- `--sources hc phac cihr` (explicit allowlist; subset selection)
- `--max-create-per-run N` (defaults to number of selected sources)

**Idempotency rules**

For each source in the allowlist:

- If a job exists for the same `campaign_year` (recorded in `ArchiveJob.config`)
  → skip.
- If a job exists with the same would-be annual job name (e.g. `hc-20270101`)
  → skip (prevents duplicates even if the job predates `campaign_year` metadata).
- If an “active” job exists for that source (queued/running/completed/indexing
  /index_failed/retryable) → skip and report why.

**Job labeling**

- Job name must include the campaign date `YYYY0101` even if the scheduler runs
  late (e.g. after reboot).
- Record metadata in `ArchiveJob.config` (no schema change):
  - `campaign_kind="annual"`
  - `campaign_year=YYYY`
  - `campaign_date="YYYY-01-01"`
  - `campaign_date_utc="YYYY-01-01T00:00:00Z"`
  - `scheduler_version="v1"`

**Ordering**

- Create jobs in the order defined in `annual-campaign.md` to make queue
  processing predictable with a single worker.

**Tests**

- Idempotency: second apply creates 0 jobs.
- Active-job skip: if a job is in progress, scheduler does not add another.
- Ordering: created jobs are in the expected order.
- Year labeling: job name/config reflect the specified year, not “now”.

**Acceptance criteria**

- Dry-run output is readable and complete (operator can review before applying).
- Apply mode creates exactly one job per selected source, unless prevented by
  the idempotency/active-job guards or `--max-create-per-run`.

**Rollback**

- If jobs were created incorrectly, use admin tooling to mark them failed or
  delete rows only if you have a safe procedure (prefer “leave rows, don’t run
  them” over ad-hoc deletion).

---

## Step 4 (of 9) — Add annual status/reporting CLI (operability)

**Objective**

Make it trivial to answer:

- “Is the annual snapshot searchable yet?”
- “Which source is stuck, and where?”

**Proposed CLI**

- `ha-backend annual-status --year YYYY [--json] [--sources ...]`

Reports per source:

- job id/name
- job status + timestamps
- retry_count
- indexed_page_count
- crawl/index exit codes if applicable

Campaign-level summary:

- total sources, indexed count, failed count, in-progress count
- “ready for search” boolean (all indexed)

**Implementation notes (v1)**

- Uses `ArchiveJob.config` metadata written by `schedule-annual`:
  - `campaign_kind="annual"`
  - `campaign_year=YYYY`
- Fallback: if metadata is missing, it will also consider the canonical annual
  name format (e.g. `hc-20270101`).
- If no annual job is found for a source, the command will also surface the
  most recent “active” job for that source (queued/running/completed/indexing/
  index_failed/retryable) to help explain why scheduling may have been skipped.
- If multiple annual candidates are found for a source/year, the command prints
  an error for that source (operators must resolve duplicates).

**Acceptance criteria**

- An operator can copy/paste the output into an incident note and it’s
  self-explanatory.

---

## Step 5 (of 9) — Production systemd timer for Jan 01 scheduling (infrastructure)

**Objective**

Run the annual scheduler automatically on Jan 01 UTC, reliably.

**systemd units (draft)**

Templates live in: `../deployment/systemd/`

- `healtharchive-schedule-annual.service` (apply)
  - Runs: `ha-backend schedule-annual --apply --year <UTC_YEAR> --sources hc phac cihr`
    - `<UTC_YEAR>` is computed at runtime (`date -u +%Y`) so that
      `Persistent=true` can safely run a missed activation after a reboot.
  - Uses `EnvironmentFile=/etc/healtharchive/backend.env`
  - Gated by `ConditionPathExists=/etc/healtharchive/automation-enabled`
  - Uses `RefuseManualStart=yes` to reduce accidental production scheduling.
- `healtharchive-schedule-annual-dry-run.service` (safe validation)
  - Runs the same scheduler without `--apply` (no DB writes).
- `healtharchive-schedule-annual.timer`
  - `OnCalendar=*-01-01 00:05:00 UTC`
  - `Persistent=true`

**Why `Persistent=true`**

- If the VPS reboots or the timer is disabled temporarily, systemd will run the
  missed activation on the next boot/start, but the scheduler still labels jobs
  as Jan 01 for the target year.

**Acceptance criteria**

- Timer wiring is validated by running the dry-run service manually:
  `systemctl start healtharchive-schedule-annual-dry-run.service`
- Timer is enabled only after manual review.

**Rollback**

- Disable timer: `systemctl disable --now healtharchive-schedule-annual.timer`
- Remove `/etc/healtharchive/automation-enabled` to stop all automation quickly.

---

## Step 6 (of 9) — Resource policy during campaign (keep site up without “safe window”)

**Objective**

Annual crawls may run for days. We want the public API and frontend to remain
available even if performance is degraded.

**Approach**

- Prefer systemd-level prioritization over complex in-app throttling.

Actions:

- Ensure worker service runs with lower priority than API:
  - `Nice=5` or `Nice=10`
  - optionally `IOSchedulingClass=best-effort`, `IOSchedulingPriority=6`
- Keep only one worker process unless you explicitly decide to accept more
  contention for a tighter “same moment” capture.

**Implementation (v1)**

- Use a systemd drop-in for the worker:
  - template: `../deployment/systemd/healtharchive-worker.service.override.conf`
  - install path: `/etc/systemd/system/healtharchive-worker.service.d/override.conf`

**Acceptance criteria**

- API stays responsive (no sustained 5xx/timeouts attributable to worker load).

---

## Step 7 (of 9) — Post-campaign “search readiness” verification (light automation)

**Objective**

After all annual jobs are indexed, capture evidence that search is working and
stable.

Implementation (v1):

- `scripts/search-eval-capture.sh` now supports `--run-id ID` so you can place
  captures under a stable, year-tagged path (instead of a nested timestamp you
  need to “discover” after the fact).
- `scripts/annual-search-verify.sh` wraps the flow safely:
  - runs `ha-backend annual-status --year YYYY --json`,
  - refuses to capture unless `summary.readyForSearch=true` (unless you pass
    `--allow-not-ready`),
  - writes `annual-status.json`/`annual-status.txt` into the same capture dir as
    the golden query responses,
  - passes optional args through to `search-eval-capture.sh`.

Recommended artifact layout on the VPS:

- `/srv/healtharchive/ops/search-eval/<year>/<run_id>/`
  - `annual-status.json`
  - `annual-status.txt`
  - `annual-search-verify.meta.txt`
  - `meta.txt` (from `search-eval-capture.sh`)
  - `<query>.(pages|snapshots).json`

Operator command (production example):

```bash
set -a; source /etc/healtharchive/backend.env; set +a
cd /opt/healtharchive-backend
./scripts/annual-search-verify.sh --year 2026 --out-root /srv/healtharchive/ops/search-eval --base-url http://127.0.0.1:8001
```

Optional (Postgres, manual): consider running a one-time `VACUUM (ANALYZE)`
after large ingestion completes. Do this manually, off-peak, and only if you’re
confident it won’t starve IO for user traffic.

**Acceptance criteria**

- You have a year-tagged artifact directory showing `annual-status` output plus
  captured `/api/search` JSON for the golden query set.

---

## Step 8 (of 9) — Replay/preview reconciliation (after search is stable)

**Objective**

Make replay and previews converge to correct state without risking core search
availability.

Implementation (v1) (aligns with `replay-and-preview-automation-plan.md`):

- New ops command: `ha-backend replay-reconcile`
  - default mode is **dry-run** (safe): prints what it would do.
  - `--apply` performs the actions.
  - global lock file prevents concurrent runs (default:
    `/srv/healtharchive/replay/.locks/replay-reconcile.lock`).
  - caps:
    - `--max-jobs N` (default 1) limits replay indexing repairs per run.
    - optional `--previews --max-previews N` (default 1) generates missing
      preview images for `/archive` source cards (still capped).
  - allowlists:
    - `--sources hc phac ...`
    - `--job-id 123 456 ...`
    - optional `--campaign-year YYYY` for annual-only reconciliation.

- Replay indexing metadata:
  - `ha-backend replay-index-job --id <id>` now writes a marker file under the
    collection root: `replay-index.meta.json` (WARC count + hash + timestamps).
  - `replay-reconcile --verify-warc-hash` can use the marker to detect drift
    (slower; optional).

Staged rollout:

1. Dry-run:
   - `ha-backend replay-reconcile --collections-dir /srv/healtharchive/replay/collections`
2. Apply for one job (manual allowlist):
   - `ha-backend replay-reconcile --apply --job-id <JOB_ID> --max-jobs 1`
3. Timer with caps (templates under `docs/deployment/systemd/`; disabled by default).
4. Optional previews (still capped; failures are surfaced clearly).

---

## Step 9 (of 9) — Deployment automation (low-cost, low-risk first)

**Objective**

Reduce operator error in backend deployments without introducing brittle
GitHub→VPS automation.

Implementation (v1)

- A single-VPS deploy helper script now exists:
  - `scripts/vps-deploy.sh`
- It is **dry-run by default**; use `--apply` to actually deploy.
- It supports:
  - fast-forward deploys (`git pull --ff-only`), or pinned SHAs via `--ref`
  - dependency install (editable) + optional skip flags
  - Alembic migrations (sources `/etc/healtharchive/backend.env` but does not print it)
  - systemd restarts for API + worker
  - a final `/api/health` check
  - a deploy lock file to avoid concurrent deploys

Operator usage (production):

```bash
cd /opt/healtharchive-backend

# Dry-run:
./scripts/vps-deploy.sh

# Deploy latest main:
./scripts/vps-deploy.sh --apply

# Deploy pinned SHA:
./scripts/vps-deploy.sh --apply --ref <GIT_SHA>
```

Future (optional, higher-risk without staging):

- GitHub Actions deployments can be considered later, but require secrets,
  rollback discipline, and careful failure handling. For now, the recommended
  posture is “boring manual deploy with a single trusted script”.
