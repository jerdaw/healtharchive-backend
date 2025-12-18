# Replay + Preview Automation (Plan Only)

Status: **design draft** (v1 implemented: `ha-backend replay-reconcile`; automation timers remain optional).

This document is a **thorough, safety-first plan** for automating the operational steps that make HealthArchive “feel like a real site archive”:

- pywb replay is kept up-to-date for each indexed crawl job (“edition”).
- cached homepage preview images exist for the `/archive` source cards.
- drift is detected and repaired safely (or surfaced clearly when it can’t be repaired).

It intentionally contains **no implementation code**. The goal is to agree on the *what/where/how/why*, guardrails, and edge cases before we build anything.

Related runbooks and context:

- Replay runbook: `docs/deployment/replay-service-pywb.md`
- Production VPS runbook: `docs/deployment/production-single-vps.md`
- Legacy imports: `docs/operations/legacy-crawl-imports.md`
- Architecture overview: `docs/architecture.md`

---

## 0) What “done” means for automation (high level)

Automation is considered “successful” when:

1. For every `ArchiveJob` with `status=indexed`, replay is *eventually* available at `https://replay.healtharchive.ca/job-<id>/...` (or we can point to a specific reason why it isn’t).
2. For every source shown on `/archive`, a cached preview image is *eventually* available for the latest edition (or we can point to a specific reason why it isn’t).
3. The system is **safe-by-default**:
   - read-only / dry-run modes exist,
   - destructive operations are excluded (cleanup),
   - retries are bounded and don’t spam,
   - concurrency is controlled,
   - we can disable automation instantly without affecting core API availability.

---

## 1) Scope: what we will and will not automate (yet)

### In scope

- **Replay indexing**: making `job-<id>` collections replayable (symlinks + CDX index).
- **Preview generation**: producing cached preview images for the `/archive` source cards.
- **Reconciliation**: a periodic “repair drift” process that converges the system to correctness.
- **Observability**: basic monitoring/alerting for replay/previews.
- **Guardrails**: locks, refusal rules, throttling, idempotency, backoff.

### Out of scope (for now)

- Crawl scheduling / job creation automation (e.g., monthly crawls).
- Any “automatic cleanup” that deletes WARCs or temp dirs without a retention policy.
- CI/CD pipeline automation (PR checks, deployment pipelines) beyond documentation.
- Anything that requires printing secrets or env files in logs or scripts.

---

## 2) Terminology (shared language)

- **Job**: `ArchiveJob` row in the DB. An indexed job corresponds to captured WARCs + `Snapshot` rows.
- **Edition**: a user-facing term for a job’s backup (one job → one edition).
- **Collection**: pywb collection name. We use `job-<id>` for a job’s collection.
- **Replay indexed**: pywb has a CDX index for `job-<id>` and can serve captures from that job’s WARCs.
- **Preview**: a cached image (PNG/JPEG/WebP) used in the `/archive` source cards.
- **Drift**: DB says job is indexed, but replay/preview state doesn’t match (missing index, missing WARCs, stale preview, etc.).

---

## 3) Current reality (constraints we must respect)

### Replay depends on WARCs staying on disk

Replay reads from job WARCs on disk. If WARCs are deleted, replay will break even if the DB still has snapshots.

- Guardrail already exists: `ha-backend cleanup-job --mode temp` refuses unless `--force` when replay is enabled.
- Operational posture: treat WARC retention as “critical state” until we design cold storage replay.

### pywb deployment and permissions matter

On the VPS (per `docs/deployment/replay-service-pywb.md`):

- pywb runs in Docker as container `healtharchive-replay`
- exposed locally at `127.0.0.1:8090`
- WARCs are mounted read-only at `/warcs` (host `/srv/healtharchive/jobs`)
- replay state is mounted read-write at `/webarchive` (host `/srv/healtharchive/replay`)
- container runs without Linux capabilities (`--cap-drop=ALL`) → file permissions must be correct; “root in container” can’t bypass them.

### Preview files are served by the backend API

The backend supports cached preview images via:

- `GET /api/sources/{source_code}/preview?jobId=<id>`

Files are expected in:

- `HEALTHARCHIVE_REPLAY_PREVIEW_DIR`
- naming convention: `source-<code>-job-<jobId>.webp|.jpg|.jpeg|.png`

The frontend expects `entryPreviewUrl` to be present in `GET /api/sources` once previews exist.

---

## 4) Non-negotiable design principles

### Safety-first defaults

- All automation must support **dry-run** mode.
- All automation must support **allowlists**:
  - allowlist by source code (`hc`, `cihr`, …)
  - allowlist by job id range or “newest N”
- All automation must refuse to run when required dependencies aren’t healthy (docker down, pywb missing, disk low, etc.).

### Idempotency

Every action must be safe to re-run:

- “Make replayable” can run repeatedly; it should converge to correct symlinks + index.
- “Generate preview” can run repeatedly; it should be atomic and overwrite safely.

### Isolation from user traffic

Automation must never run on:

- an API request path,
- a frontend request path,
- or any flow that could block interactive user browsing.

It must run as a background process (manual trigger, timer, or separate worker queue).

### Observability

Automation must produce:

- machine-readable status (“OK / needs work / blocked”) per job and per source,
- actionable error messages (without secrets),
- backoff to avoid repeated failures.

---

## 5) Automation candidate A — Replay indexing (pywb collections + CDX)

### 5.1 Desired end state

For each job `id` where `ArchiveJob.status == indexed`:

- A pywb collection exists: `/srv/healtharchive/replay/collections/job-<id>/...`
- The collection contains symlinks in `archive/` pointing to `/warcs/...` WARC paths (container-visible).
- `indexes/index.cdxj` exists and corresponds to the current WARC set.
- A basic replay check succeeds for the job’s entry URL:
  - timegate form: `https://replay.healtharchive.ca/job-<id>/<original_url>`
  - (optional) CDX query returns at least one record for the entry URL.

### 5.2 Where this automation should live (options)

**Option A1: Reconciler timer (recommended first)**

- A periodic process that looks at “desired jobs” vs “replay-ready jobs” and repairs drift.

Why this is the best first automation:

- decoupled from crawl/indexing,
- can be disabled instantly,
- can backfill older jobs,
- naturally repairs operator mistakes (deleted collections/indexes).

**Option A2: Worker hook (later, only if needed)**

- After indexing completes, automatically trigger replay indexing.

Risk:

- couples two heavy operations (index + replay indexing),
- increases failure surface in the worker loop,
- needs robust retry/backoff to avoid wedging jobs.

### 5.3 Required guardrails

**Concurrency**

- Global lock: only one replay indexing operation at a time.
- Per-job lock: prevent two reindex attempts for the same `job-<id>`.

Implementation decision (when we code):

- Prefer `flock`-based lock files under `/srv/healtharchive/replay/.locks/`.
  - simple, visible, and resilient across process crashes.

**Eligibility rules (must be true to proceed)**

- Job exists and is `status=indexed`.
- WARC discovery finds >= 1 `.warc.gz` file.
- pywb container is running (or is startable).
- The process has:
  - write access to `/srv/healtharchive/replay/collections/…`
  - permission to run `docker exec` for `wb-manager`.

**Refusal rules (stop early, report why)**

- Disk below a configured threshold (to prevent filling the VPS root disk).
- WARCs are missing / unreadable (likely cleanup ran) → mark job “replay blocked: missing data”.
- pywb container exists but is unhealthy (restarts / crashes repeatedly).

**Resource control**

- Cap “jobs per run” (e.g., 1–2 per run initially).
- Optionally run with `nice` and/or `ionice` if indexing impacts API latency.

**Failure handling**

- Classify failures into a small set:
  - “blocked” (needs human action: missing WARCs, permissions)
  - “retryable” (transient: docker restart, pywb busy)
  - “internal” (bug: unexpected exception)
- Exponential backoff for retryable failures (and a ceiling).
- Suppress repeated identical errors from spamming journald.

### 5.4 State tracking (how we know what’s done)

We need to know:

- which jobs are already replay indexed,
- whether their replay index matches their current WARC list,
- and when we last attempted/failed.

Two viable designs:

**A) Filesystem marker (minimal, first iteration)**

- After successful replay indexing, write a small JSON marker file into the collection:
  - `collections/job-<id>/replay-index.meta.json`
  - includes:
    - `jobId`
    - `indexedAt`
    - `warcCount`
    - `warcListHash` (hash of sorted WARC paths)
    - `pywbVersion` (optional)

Pros:

- doesn’t require DB migrations,
- works even if DB is temporarily unavailable (but replay indexing requires DB anyway).

Cons:

- harder to report status through APIs/admin dashboards,
- harder to query across jobs.

**B) DB state (preferred once we implement automation seriously)**

- Add DB fields or a dedicated table to track replay indexing state.

Pros:

- easy observability (admin endpoints, metrics),
- easier to reconcile at scale,
- can store retry counts and next-attempt timestamps.

Cons:

- requires migration and careful rollout.

### 5.5 Edge cases to explicitly handle

- **WARCs deleted after indexing**: DB says “indexed”, replay 404s.
  - Detect via WARC discovery read failures.
  - Mark “blocked: missing WARCs”; do not retry aggressively.
- **Permissions drift**: pywb cannot read WARCs due to chmod/chown changes.
  - Detect by trying to `stat`/open a sample WARC (host) or via pywb reindex failure.
  - Mark “blocked: permissions”; provide a runbook to fix.
- **Job re-imported / WARC set changes**: new WARCs added or paths differ.
  - Detect via `warcListHash` change; reindex.
- **Disk pressure**: CDX can be large.
  - Refuse below threshold; alert.
- **pywb container restart during reindex**:
  - Reindex is rerunnable; ensure partial index doesn’t block future runs.

---

## 6) Automation candidate B — Cached source preview generation

### 6.1 Desired end state

For each source code shown on `/archive`:

- For the “current edition” job id (latest by capture date):
  - a preview file exists in `HEALTHARCHIVE_REPLAY_PREVIEW_DIR` named:
    - `source-<code>-job-<jobId>.webp` (preferred), or
    - a supported fallback format.
- The backend returns `entryPreviewUrl` in `GET /api/sources`.
- The frontend displays the image without embedding live iframes.

### 6.2 Where this automation should live (options)

**Option B1: On-demand operator command (recommended first)**

- Run it manually after a job becomes replayable, or when you want to refresh thumbnails.

Why:

- preview generation is inherently flaky (dynamic pages, timeouts),
- it’s easy to overwhelm the VPS if automated too aggressively.

**Option B2: Scheduled refresh timer (later)**

- Daily/weekly, only for “latest edition per source”.

Guardrail:

- cap number of previews per run,
- run during off-peak hours.

### 6.3 Guardrails required

- Always generate using a replay URL with `#ha_nobanner=1` so the screenshot matches the underlying site.
- Strict timeouts + “continue on failure”.
- Atomic writes:
  - write to `*.tmp` then `rename()` to final name.
- Validate output:
  - file exists and size > minimum threshold.
- Rate limiting:
  - cap to N previews per run.

### 6.4 Edge cases

- replay entry URL 404s (job not replay indexed yet, or missing WARCs).
- replay loads but page never settles (long-running scripts).
- some pages are heavy and render inconsistently; use fixed viewport and a small settle delay.

---

## 7) Automation candidate C — Reconciliation loop (“converge to correctness”)

This is the safest automation pattern: a background process that continuously closes the gap between “desired” and “actual” state.

### 7.1 Inputs and outputs

**Inputs**

- DB jobs and snapshots (`ArchiveJob`, `Snapshot`, `Source`)
- filesystem state:
  - pywb collections under `/srv/healtharchive/replay/collections`
  - preview files under `HEALTHARCHIVE_REPLAY_PREVIEW_DIR`

**Outputs**

- replay indexing performed for some jobs (via CLI)
- preview generation performed for some sources (optional, depending on enablement)
- status reporting (logs + metrics)

### 7.2 Reconciler modes (must exist when implemented)

- `dry-run`: compute and print planned actions only.
- `apply`: perform actions.

### 7.3 Recommended initial algorithm (when we implement)

1. Acquire a global lock (refuse if already running).
2. Query for `ArchiveJob.status=indexed` ordered newest-first.
3. For each job (up to a max-per-run):
   - if job is not replay indexed (marker/state missing or warc hash changed):
     - run replay indexing step
4. For each source (optional, up to a max-per-run):
   - determine latest edition job id (from `/api/sources/{code}/editions` or DB query)
   - if preview missing for that job id:
     - generate preview
5. Emit a summary report.

### 7.4 Guardrails

- allowlist sources for early rollouts
- max jobs per run
- max previews per run
- backoff on failures
- refuse when disk low

---

## 8) Automation candidate D — Monitoring + alerting (replay-aware)

Replay introduces new failure modes that standard API health checks won’t catch.

### 8.1 Recommended monitors

External (cheap, stable):

- `GET https://api.healtharchive.ca/api/health` (already)
- `GET https://replay.healtharchive.ca/` (200)
- `HEAD https://replay.healtharchive.ca/` (200)
- One “known good” replay entry URL per major source (200):
  - `https://replay.healtharchive.ca/job-1/.../https://www.canada.ca/en/health-canada.html`

Internal (optional):

- systemd timer that checks:
  - pywb container is running
  - disk usage below a safe threshold
  - replay CDX exists for newest job

### 8.2 Alert playbook (what to do when it breaks)

- Replay origin down:
  - check `healtharchive-replay.service` status/logs
  - check docker health
- Replay 404 for a known entry URL:
  - check that the job’s WARCs still exist
  - check that `replay-index-job` was run and index exists
- Preview missing:
  - run preview generation manually for latest job

---

## 9) Rollout strategy (methodical and safe)

1. Agree on this document.
2. Implement reconciler in `dry-run` mode only.
3. Run it manually and review output.
4. Enable `apply` mode for a single allowlisted source.
5. Add backoff and failure classification.
6. Only after it’s stable:
   - consider enabling for all sources,
   - consider adding preview generation to the reconciler,
   - (optionally) consider a worker hook if needed.

---

## 10) Do not automate cleanup until retention is designed

Cleanup is the highest-risk automation.

Before we automate any cleanup, we need a separate retention design:

- Which jobs must remain replayable and for how long?
- Where do “cold” WARCs live (NAS/object storage)?
- How do we replay cold WARCs without copying huge data back to the VPS?
- Can we move WARCs out of temp dirs so “cleanup temp state” is safe?

Until then:

- Keep cleanup manual and conservative.
- Rely on the existing CLI guardrail (`cleanup-job` refuses unless `--force` when replay is enabled).

---

## Appendix A — Manual operator playbook (current, safe)

When a new job is indexed:

1. Make it replayable:
   - `ha-backend replay-index-job --id <id>`
2. (Optional) Generate preview:
   - produce `source-<code>-job-<id>.{webp,png,jpg}` in `HEALTHARCHIVE_REPLAY_PREVIEW_DIR`
3. Verify:
   - `/snapshot/<id>` and `/browse/<id>`
   - `/archive` source cards show preview and deep browsing works
