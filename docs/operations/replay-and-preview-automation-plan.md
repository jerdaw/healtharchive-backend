# Replay + Preview Automation (Plan Only)

This document is a **design + risk assessment** for automating the “replayability” workflow in HealthArchive.

It contains **no code**. The goal is to outline *what* we might automate, *where* it should live, *how* it should behave, and the **guardrails + edge cases** required so we can iterate on the plan safely before implementing anything.

## Scope: what we are (and are not) automating

**In scope**

- Making an indexed `ArchiveJob` replayable in pywb (`job-<id>` collection + CDX index).
- Generating cached homepage preview images used by the `/archive` source cards.
- Safe “reconciliation” automation (detect drift between DB jobs and replay/previews; repair it).
- Operational guardrails (avoid deleting replay WARCs; safe retries; throttling; locks).
- Monitoring and alerting for replay-specific failure modes.

**Out of scope (for now)**

- Automated crawling / job scheduling (crawl frequency, queue management).
- Any destructive cleanup automation (deleting WARCs / temp dirs) without a dedicated retention design.
- Anything that would require secrets to be printed to logs or output.
- CI/CD changes (GitHub Actions, deployment pipelines) beyond documenting what we’d want.

## Current reality (baseline)

### Replay “replayable job” definition

We consider a job replayable if:

1. The job is `status=indexed` in the DB (snapshots exist and are queryable), **and**
2. A corresponding pywb collection exists: `job-<id>`, **and**
3. pywb has an up-to-date CDX index for that collection (typically `indexes/index.cdxj`), **and**
4. The WARCs referenced by that job remain accessible on disk (replay depends on this).

### Existing primitives we can build automation around

- Backend CLI:
  - `ha-backend replay-index-job --id <id>` (creates/refreshes the pywb collection + symlinks + CDX index).
  - `ha-backend cleanup-job --mode temp` has a guardrail: refuses unless `--force` when replay is enabled.
- API contracts used by the frontend:
  - `GET /api/sources` includes `entryBrowseUrl` and optional `entryPreviewUrl`.
  - `GET /api/sources/{code}/editions` returns per-job edition metadata + entry URL.
  - `GET /api/replay/resolve` supports “edition switching” by resolving captures in another job.
  - `GET /api/sources/{code}/preview?jobId=<id>` serves cached images from `HEALTHARCHIVE_REPLAY_PREVIEW_DIR`.
- Preview naming convention (served by the API):
  - `source-<sourceCode>-job-<jobId>.webp|.jpg|.jpeg|.png`

## Design principles (non-negotiable)

### Safety-first

- Automation must be **read-only by default** and require explicit enablement.
- Any step that can break production browsing must be behind:
  - a feature flag, and/or
  - a dry-run mode, and/or
  - a conservative allowlist (e.g., “only jobs newer than X”, “only these sources”).

### Idempotency

Every automated job should be safe to run repeatedly:

- Re-running “make replayable” should converge to the same state.
- Re-running “generate preview” should overwrite atomically and not leave partial files.
- Partial failure should be retryable without manual cleanup.

### Decoupling from the crawl/index critical path

The replayability pipeline can be expensive (CDX reindexing, preview rendering).

We should avoid coupling it tightly to:

- the worker’s indexing loop, and
- any user-facing request path.

Prefer **post-processing** or **reconciliation** loops that can be paused without breaking the core API.

### Observability and auditability

Automation should produce:

- clear “what happened” logs (no secrets),
- a way to see “which jobs are replayable / not replayable and why”,
- metrics/health signals suitable for alerting.

## Automation candidate A: replay indexing (pywb collections + CDX)

### What needs to happen

For each indexed job `id`:

1. Ensure pywb collection `job-<id>` exists (`wb-manager init`).
2. Ensure stable symlinks exist under:
   - `/srv/healtharchive/replay/collections/job-<id>/archive/`
   - pointing to container-visible WARC paths under `/warcs/...` (host `/srv/healtharchive/jobs/...`).
3. Run `wb-manager reindex job-<id>` to build/update CDXJ.

We already encapsulate this as:

- `ha-backend replay-index-job --id <id>`

### Where the automation should live (options)

#### Option A1: “Reconciler” (recommended first)

A periodic process (systemd timer) that reconciles desired vs actual state:

- Desired set: all `ArchiveJob.status=indexed` (possibly filtered by source or age).
- Actual state: pywb collection + CDX index exists and matches the job’s current WARC set.

Pros:

- Safest. You can pause/disable without impacting indexing.
- Can backfill old jobs.
- Naturally handles drift (someone deletes collection/index; replay breaks; reconciler fixes).

Cons:

- Can take time to “catch up” after a job finishes indexing (runs on a schedule).

#### Option A2: Worker hook (not recommended as the first automation)

After `index_job(job_id)` completes successfully, enqueue a replay-index step.

Pros:

- Fast availability: replay becomes usable immediately after indexing finishes.

Cons:

- Raises complexity in the worker loop and failure semantics.
- If replay indexing fails, you need a robust retry/backoff mechanism to avoid wedging the worker.

#### Option A3: Manual-but-assisted (intermediate step)

No automation, but improve operator flow:

- add a runbook checklist and one “replay all missing” command,
- keep it explicitly human-triggered during early operations.

### Guardrails required

#### Concurrency locks

- Ensure only one replay-index runs at a time globally, or at least per job.
- Avoid multiple processes attempting to reindex the same collection concurrently.

Implementation ideas (choose one when coding):

- lockfile (e.g., `/srv/healtharchive/replay/.locks/replay-index-job-<id>.lock`)
- `flock` wrapper around the command
- database-level “in-progress” marker row (more complex but centralized)

#### Job eligibility and refusal rules

- Only run on jobs with:
  - `ArchiveJob.status == indexed`
  - a non-null `output_dir`
  - WARC discovery finds at least 1 WARC
- Refuse when:
  - replay service/container isn’t running,
  - WARC files are missing/unreadable (likely cleanup ran),
  - disk is critically low (avoid filling root and killing services).

#### Throttling and resource caps

CDX indexing for large jobs can be CPU/disk heavy. Automation must:

- enforce `nice` / `ionice` if needed,
- cap concurrency to 1,
- optionally cap maximum jobs per run.

#### Failure handling and retries

- On failure, record an error state somewhere (see “state tracking”).
- Use exponential backoff.
- Avoid infinite loops (e.g., “fail 1000 times per hour”).
- Don’t spam logs on persistent failures.

### State tracking (how do we know what’s done?)

We need an explicit definition of “replay indexed for job `<id>`” that is safe under drift.

Minimum viable approach (filesystem-based):

- Treat `.../collections/job-<id>/indexes/index.cdxj` existence as “indexed”.
- Additionally write a small metadata marker (e.g., `replay-index.meta.json`) that includes:
  - job id
  - timestamp
  - number of WARCs linked
  - a hash of the WARC path list (so changes trigger reindex)

More robust approach (DB-based):

- Add DB fields (or a small table) to track:
  - `replay_indexed_at`
  - `replay_index_status` (`ok|error|in_progress`)
  - `replay_index_error` (truncated message)
  - `replay_index_warc_hash`

We should prefer DB-based once we commit to automation, because it makes:

- dashboards and API reporting easier,
- reconciliation queries cheap and safe.

### Edge cases to explicitly handle

- **WARCs were deleted** after indexing (cleanup ran): DB still has snapshots but replay is broken.
  - Automation should detect “WARCs missing” and mark job as “replay unavailable (data missing)”.
- **Job reindexed / imported again**: the WARC set can change (new files, different paths).
  - Automation should reindex if the warc-list hash changes.
- **Permissions drift**: pywb runs without Linux capabilities; “root in container” can’t bypass perms.
  - Automation must verify group readability before reindexing to avoid long failures.
- **Disk full**: CDX indexes can be large (hundreds of MB).
  - Automation should refuse when below a threshold.
- **Container restarts mid-index**:
  - Ensure retries don’t corrupt state; reindex should be repeatable.
- **Very large jobs**:
  - Consider splitting indexing schedule windows; consider alternative indexing strategies later.

## Automation candidate B: cached source preview generation

### What needs to happen

For each source `code` and for each “current edition” job id:

- Render `entryBrowseUrl` (a replay URL) in a headless browser and capture a screenshot.
- Strip the replay banner from the screenshot (already supported by our preview script).
- Save image as `source-<code>-job-<id>.png` or `.webp` (preferred long-term).
- Write atomically:
  - save to `...tmp` then `rename()` to final filename.

### Where the automation should live (options)

#### Option B1: On-demand operator command (recommended first)

You run it when needed (e.g., after a new job is indexed + replay indexed).

Pros:

- Zero background load.
- Easy to stop if a page causes Playwright issues.

Cons:

- Manual step.

#### Option B2: Scheduled “preview refresh” timer

Run daily/weekly:

- fetch `/api/sources` (local)
- generate previews for any missing `source-*-job-*.{png,webp}` files
- optionally refresh previews for newest jobs only

Pros:

- Simple and decoupled.

Cons:

- Playwright runs can be heavy.

#### Option B3: Triggered after replay indexing completes

Pros:

- Previews become available quickly.

Cons:

- Couples two expensive operations; increases failure surface.

### Guardrails required

- **Timeouts**: replay pages can be slow; must time out and continue.
- **Banner exclusion**:
  - always load with `#ha_nobanner=1`, *and* remove banner element in-page as a fallback.
- **Rate limiting**:
  - avoid generating previews for many sources at once during peak hours.
- **Safety**:
  - do not follow third-party navigation (only the given replay URL).
- **Output validation**:
  - ensure output file size is “reasonable” (not 0 bytes).

### Edge cases

- Pages that never settle due to long-running scripts.
- Missing captures for the entry page (replay 404).
- Heavy animations causing blurry screenshot (consider “wait settle” delay).
- Different aspect ratios for different sites; pick a fixed viewport that works for most.

## Automation candidate C: reconciliation loop (“keep reality correct”)

This is the automation pattern that reduces operational burden without requiring perfect event triggers.

### Desired outcomes

- Every `indexed` job *eventually* becomes replayable, or we have a clear “why not”.
- Every “source card” *eventually* has a preview image, or we have a clear “why not”.

### Reconciler checklist per run

For each source:

- Fetch editions (`/api/sources/{code}/editions`) and pick “latest edition” by `lastCapture`.
- Ensure replay exists for that edition:
  - ensure `entryBrowseUrl` works (optional lightweight HTTP check).
- Ensure preview exists:
  - check for `source-<code>-job-<id>.*` in preview dir.

For each job:

- If job is indexed but missing replay index marker, run `replay-index-job`.
- If it repeatedly fails, stop retrying frequently and surface the error.

### Guardrails

- Run at low frequency to start (e.g., hourly or daily).
- Hard cap “jobs processed per run”.
- “Never run two reconciliers simultaneously” lock.

## Automation candidate D: monitoring + alerting

Replay introduces new failure modes that can be detected cheaply:

- Replay origin down (pywb service stopped).
- Replay origin not embeddable (headers misconfigured).
- “BrowseUrl” generation breaks (backend env missing; code regression).
- Replay returns 404 for a known entry page (WARCs missing or index missing).

Suggested monitors (external):

- `GET https://api.healtharchive.ca/api/health` (already)
- `GET https://replay.healtharchive.ca/` (should be 200)
- `HEAD https://replay.healtharchive.ca/` (should be 200)
- A single known replay entry URL:
  - `https://replay.healtharchive.ca/job-1/.../https://www.canada.ca/en/health-canada.html`

Suggested internal (optional):

- Systemd timers that log a one-line “OK/FAIL” heartbeat.

## Rollout strategy (how we implement safely)

1. **Document-only** (this file): agree on semantics and guardrails.
2. Implement a reconciler in **dry-run** mode:
   - prints what it *would* do, never executes.
3. Enable on **one source** (allowlist), low frequency.
4. Add metrics/logging and error backoff.
5. Expand allowlist gradually.
6. Remember: “working once” is not stable. Run through failure scenarios intentionally.

## “Do not automate yet” warning: cleanup and retention

Cleanup is the most dangerous automation.

Before we automate any cleanup, we need a clear policy:

- Which jobs must remain replayable and for how long?
- Where do “cold” WARCs go (NAS/object storage) and how do we replay them?
- Can we move WARCs out of temp dirs so `cleanup-job` can safely delete temp state?

Until that policy exists:

- Keep cleanup manual and conservative.
- Use the existing CLI guardrail (`cleanup-job` refuses unless `--force` when replay is enabled).

## Appendix: minimal operator playbooks (manual, safe)

When a new job is indexed:

1. Make it replayable:
   - run `ha-backend replay-index-job --id <id>`
2. (Optional) Generate preview:
   - render `entryBrowseUrl` with the existing Playwright tooling
   - save `source-<code>-job-<id>.png` in `HEALTHARCHIVE_REPLAY_PREVIEW_DIR`
3. Verify in browser:
   - `/snapshot/<id>`
   - `/browse/<id>`
   - `/archive` source cards

