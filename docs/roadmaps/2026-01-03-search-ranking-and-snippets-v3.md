# Search ranking + snippet quality iteration (v3) — implementation plan

Status: **in-progress** (created 2026-01-03, implementation started 2026-01-18)

Note: **Do not deploy/implement this plan on the production VPS until the annual scrape/crawl is finished and the campaign jobs are `indexed`.**

## Goal

Iterate on:

- **Search relevance** (especially broad “hub intent” queries like `covid`) using Postgres FTS + lightweight heuristics, without introducing a separate search service.
- **Snippet quality** so results “feel human” (less navigation/cookie/banner boilerplate; more meaningful page content).

Deliver this as:

- A new ranking version **v3** (opt-in via `ranking=v3`, later set as default via `HA_SEARCH_RANKING_VERSION=v3` after evaluation).
- Index-time extraction improvements for `Snapshot.title` / `Snapshot.snippet` / `Snapshot.language` (and optionally a derived archived flag), with safe refresh/backfill workflows.
- A repeatable, artifacts-backed evaluation loop using existing golden query tooling.

This plan is intentionally **sequential**: complete each phase before starting the next.

## Why this is “next” (roadmap selection)

This is the next highest-leverage item in `docs/roadmaps/roadmap.md` that is:

- Implementable in git (unlike external/IRL partnership work),
- High impact for core project purpose (research citations + change tracking depend on discoverability),
- Compatible with single‑VPS constraints and current architecture (Postgres FTS + heuristics),
- Not “automation work” (explicitly excluded for now).

## Docs setup (do first, before coding)

This repo separates backlog vs implementation plans vs canonical docs to avoid drift
(see `../documentation-guidelines.md`).

1) **Create this plan doc**
- File: `docs/roadmaps/2026-01-03-search-ranking-and-snippets-v3.md` (this document)

2) **Backlog linkage**
- Update `docs/roadmaps/roadmap.md`:
  - Replace the “Search ranking + snippet quality iteration…” backlog bullet with a link to this plan, marked “in progress”.

3) **Roadmaps index**
- Update `docs/roadmaps/README.md` to list this plan under “Implementation plans (active)”.

4) **Canonical docs to keep accurate during/after implementation**
- Ops evaluation:
  - `docs/operations/search-quality.md`
  - `docs/operations/search-golden-queries.md`
- Deployment/runbooks:
  - `docs/deployment/search-rollout.md` (extend from v2 → v3)
  - `docs/architecture.md` (search section remains accurate)

Rule: keep canonical docs describing **what exists and how to use it**; keep this plan
describing **what we will do and in what order**.

---

## Scope, goals, constraints

### In-scope outcomes (what we will deliver)

- **Snippets that “feel human”** for common queries:
  - less nav/boilerplate (e.g., “Skip to content … Menu … Search …”),
  - more meaningful page body text,
  - stable and reasonably readable across sources.
- **Search relevance improvement** for curated golden queries using repeatable captures + diffs.
- **Ranking version v3**:
  - `ranking=v3` works for `/api/search` and `/api/admin/search-debug`,
  - later make v3 the default via `HA_SEARCH_RANKING_VERSION=v3` after evaluation.
- **Repeatable evaluation artifacts**:
  - captures + diff reports stored outside git (e.g., `/srv/healtharchive/ops/search-eval`),
  - documented pass/fail rubric for changes.

### Non-goals (explicitly out of scope)

- No new search infrastructure (no Elasticsearch/Meilisearch/etc.).
- No query logging system (golden queries remain curated).
- No ops automation/timers/healthchecks work in this effort.
- No major re-architecture of WARC storage/replay retention.
- No large UX redesign (frontend already supports `view=pages|snapshots`, `includeNon2xx`, `includeDuplicates`).

### Constraints to respect (project resources + policy)

- **Single-VPS reality** + Postgres FTS is the core engine (see `docs/operations/search-quality.md`).
- **Performance budgets** (from `docs/operations/growth-constraints.md`):
  - Search (view=pages): p95 target < 2s for common queries (regression guardrail).
- **Provenance and trustworthiness**:
  - Index-time metadata changes are allowed, but replay fidelity and WARC linkage must remain intact.
- **Minimal operational complexity**:
  - Rollout and rollback must be simple and reversible.

---

## Current-state map (what exists today, to build on)

### Search surfaces

- Public endpoint: `GET /api/search`
  - Implementation: `src/ha_backend/api/routes_public.py`
- Admin debug endpoint: `GET /api/admin/search-debug`
  - Implementation: `src/ha_backend/api/routes_admin.py`

### Ranking versions and configuration

- Ranking config and query-mode logic:
  - `src/ha_backend/search_ranking.py` (`v1`, `v2`)
- Default ranking via env var:
  - `HA_SEARCH_RANKING_VERSION` (see `docs/deployment/search-rollout.md`)

### Golden query evaluation tooling

- Evaluation docs:
  - `docs/operations/search-quality.md`
  - `docs/operations/search-golden-queries.md`
- Scripts:
  - Capture: `scripts/search-eval-capture.sh`
  - Capture+diff wrapper: `scripts/search-eval-run.sh`
  - Diff: `scripts/search-eval-diff.py`

### Snippet extraction and indexing pipeline

- HTML → title/text/snippet/language extraction:
  - `src/ha_backend/indexing/text_extraction.py`
- Indexing pipeline uses extraction:
  - `src/ha_backend/indexing/pipeline.py`
- Metadata refresh CLI:
  - `ha-backend refresh-snapshot-metadata --job-id <ID>` (wired in `src/ha_backend/cli.py`)

### Tests to extend (existing coverage)

- Search API behavior:
  - `tests/test_api_search_and_snapshot.py`
- Admin search debug coverage:
  - `tests/test_admin_search_debug.py`
- Fuzzy search:
  - `tests/test_search_fuzzy.py`

---

## Definition of Done (DoD) + acceptance criteria

### Relevance (golden queries)

For the curated list in `docs/operations/search-golden-queries.md`:

- Broad queries surface expected hub pages in top 10 **more consistently** under v3 than v2.
- Anti-results (obvious junk/boilerplate/error surfaces) are **less likely** to appear in top 20 for broad queries.
- No major regressions for non‑COVID guardrail queries.

### Snippet quality

On a representative sample of results from golden query captures:

- Snippets do not frequently start with navigation boilerplate.
- Snippets contain at least one meaningful sentence/phrase from the page body for common pages.
- Language detection remains at least as good as today for en/fr smoke tests.

### Performance + safety

- No meaningful p95 regression for `GET /api/search` common queries in production.
- No API schema breaks; the frontend continues working unchanged.
- Rollback remains a single env-var flip (same operational safety as v2).

---

## Recommendations for key design decisions (resolve before implementation)

These are the recommended choices given HealthArchive goals/purposes, single‑VPS resources,
and modern best practices (explicit derived features + reversible rollouts).

### 1) Introduce `Snapshot.is_archived` (recommended: **yes**, small schema change)

**Recommendation:** add a dedicated archived flag column (tri-state: `NULL` unknown, `true`, `false`)
and compute it at indexing/refresh time from title + HTML/text signals.

Why:

- Decouples snippet quality work from ranking quality. We want cleaner snippets, but we also
  want archived detection to remain reliable and explainable.
- Makes ranking cheaper and more stable than query-time `ilike()` heuristics over snippet text.
- Supports future UX or research needs (“include/exclude archived”) without re-parsing HTML.

Operational approach:

- Add `snapshots.is_archived` via Alembic migration.
- Update index-time extraction to compute it.
- Backfill existing rows via:
  - targeted `refresh-snapshot-metadata --job-id ...` on recent/high-impact jobs first, then expand,
  - or a dedicated backfill CLI if refresh is too slow for full history.
- In ranking:
  - Prefer `is_archived` when column exists and non-NULL,
  - Fall back to the existing title/snippet heuristics for rows where `is_archived IS NULL` during the transition window.

### 2) FTS vector body text length (recommended: **4KB** of cleaned main content)

**Recommendation:** feed **~4096 characters** of cleaned main-content text into Postgres FTS vectors,
in addition to title and URL, while keeping the UI snippet short (~280 chars).

Why:

- 2KB often under-captures key terms on long guidance pages; 4KB improves recall meaningfully.
- 8KB increases backfill CPU + GIN index size and can unintentionally dilute ranking via length normalization on a small VPS.
- 4KB is a conservative, modern default that still leaves headroom to tune later.

Implementation approach (conceptual):

- Keep `Snapshot.snippet` as UI-facing (short).
- Compute an internal `content_text_for_fts` from the extraction pipeline (cleaned main content),
  then truncate to 4KB before calling `to_tsvector`.
- Keep title weight highest; content weight medium; URL weight low (current pattern).

Optional (only if we need flexibility later): add a single env var like `HA_SEARCH_VECTOR_BODY_MAX_CHARS`
defaulting to `4096`, but avoid config sprawl unless we have a real tuning need.

### 3) Minimal snippet heuristics for Canada.ca without overfitting (recommended: **generic-first**)

**Recommendation:** implement a small, generic, evidence-driven set of heuristics:

1) **DOM pruning (generic)**
   - Remove `script`, `style`, `noscript` (already).
   - Remove semantic boilerplate containers: `nav`, `header`, `footer`, `aside`, `form` (already).
   - Add ARIA-role pruning: `[role=navigation]`, `[role=banner]`, `[role=contentinfo]`, `[role=search]`.

2) **Content-root selection (generic)**
   - Prefer `<main>` / `[role=main]`.
   - Else prefer `<article>`.
   - Else choose the “best” container by a simple score:
     - + text length,
     - − link density penalty,
     - − boilerplate phrase penalty.

3) **Snippet selection (generic)**
   - Build snippet from the first “good” paragraph/sentence chunk that:
     - meets a minimum length threshold,
     - contains sentence-like punctuation,
     - does not match a small bilingual boilerplate phrase list (skip links, cookie consent, “menu/search” lines),
     - is not mostly a list of navigation links.

Canada.ca-specific handling should be **additive only if needed**:

- Start generic; evaluate.
- If Canada.ca pages still leak boilerplate consistently, add a *very small* optional rule set gated by hostname (endswith `canada.ca`)
  to drop known WET wrapper regions, but only when they are clearly navigation/boilerplate (high link density, low text).

This avoids overfitting while still acknowledging the archive’s heavy Canada.ca coverage.

---

## Phase 1 — Baseline capture + problem inventory (no behavior changes yet)

Objective: establish “before” truth for relevance + snippet issues and ensure we can measure improvements.

### 1.1 Lock the evaluation protocol (what we always capture)

Decide and document (in this plan) the exact capture matrix:

- Primary user-facing relevance:
  - `view=pages`
  - `sort=relevance`
  - `pageSize=20` (or 10; pick once and keep stable)
- Debugging noise / capture-level issues:
  - `view=snapshots`
  - `sort=relevance`

Decide how diffs key items:

- For `view=pages`: prefer `originalUrl` (with URL canonicalization).
- For `view=snapshots`: prefer `id` when URL churn makes comparisons confusing.

### 1.2 Run baseline captures (production)

Use the existing scripts; store artifacts outside git.

Recommended output directory on the VPS:

- `/srv/healtharchive/ops/search-eval`

Baseline runs to capture:

- `v1` vs `v2` (explicit `ranking=v1` and `ranking=v2`), even if v2 is already default.
- (Optional) a “default” capture (no `ranking=`) to confirm env wiring.

Artifacts to keep:

- Capture dirs: `<run-id>-v1/`, `<run-id>-v2/`
- Diff report: `<run-id>.diff.txt`
- Notes: `<run-id>.notes.md` summarizing:
  - top obvious improvements/regressions,
  - snippet failure examples,
  - any surprising anti-results.

### 1.3 Create a snippet failure taxonomy (evidence-driven)

From baseline captures, classify failures with real examples:

- Nav/header boilerplate leaks into snippet.
- Cookie banners / consent text dominates snippet.
- “Archived page” banner dominates snippet (English/French variants).
- Empty/near-empty snippet despite content.
- Garbage whitespace / repeated tokens / broken decoding.

For each category, record 3–10 representative URLs (or snapshot IDs) and the observed snippet text.

### 1.4 Establish a lightweight performance baseline

Record at least one timing snapshot for:

- broad query: `covid` (`view=pages`)
- medium query: `covid vaccine`
- French query: `grippe`

Goal: detect “obvious” regressions later, not produce a full benchmark suite.

**Deliverables:**

- Baseline capture dirs + diff reports in `/srv/healtharchive/ops/search-eval`
- Taxonomy + example list in this plan doc (append a short section under Phase 1)
- A short “baseline timing” note

**Exit criteria:** agreement on what “better” means and which snippet failures matter most.

---

## Phase 2 — Design: snippet extraction improvements (index-time, minimal-risk)

Objective: define the smallest reliable extraction upgrade that improves snippets without destabilizing indexing.

### 2.1 Design principles

- Keep dependencies minimal (prefer improving BeautifulSoup heuristics over adding heavy libraries).
- Prefer deterministic extraction that behaves similarly across runs.
- Avoid query-time snippet generation (too expensive unless we store more text).
- Preserve provenance: snippets are derived metadata; do not alter WARC linkage or replay.

### 2.2 Extraction algorithm upgrade (target: `src/ha_backend/indexing/text_extraction.py`)

Define (before coding) the concrete strategy:

1) Pre-clean DOM:
   - Remove `script`, `style`, `noscript`.
   - Remove semantic boilerplate tags: `nav`, `header`, `footer`, `aside`, `form`.
   - Remove ARIA boilerplate roles: navigation/banner/contentinfo/search.

2) Choose a content root:
   - Prefer `<main>` or `[role=main]`.
   - Else prefer `<article>`.
   - Else score candidate containers (`article`, `section`, `div`) and pick the best:
     - text length (positive),
     - punctuation density (positive),
     - link density (negative),
     - boilerplate phrase match (negative).

3) Extract candidate blocks:
   - Prefer paragraph-like blocks (`p`, headings + following paragraphs) rather than full-page text.
   - Preserve enough context for a meaningful snippet, but avoid dumping navigation lists.

4) Snippet selection:
   - Choose the first “good” block meeting criteria; fall back to next best; final fallback to current behavior.
   - Ensure the snippet does not start with known boilerplate phrases.
   - Keep stable max length (e.g., ~280 chars) and clean whitespace.

### 2.3 Archived detection decision (commit to recommendation)

Implement `Snapshot.is_archived` (Phase 3 will cover details). Snippet extraction should
not need to keep archived banner text at the top in order to support ranking.

### 2.4 FTS vector input decision (commit to recommendation)

Feed ~4KB of cleaned main content text into the search vector computation.

**Deliverables:**

- A written extraction spec (heuristics + thresholds + bilingual boilerplate phrase list)
- A written archived detection spec (signals + conservatism rules)
- A written FTS text input spec (where content comes from; how it is truncated)

**Exit criteria:** we can predict how the changes address the Phase‑1 failures.

---

## Phase 3 — Implement + test: snippet extraction improvements + archived flag

Objective: implement extraction changes safely with strong tests and a refresh/backfill path.

### 3.1 Implement extraction changes (code)

Files likely involved:

- `src/ha_backend/indexing/text_extraction.py`
- `src/ha_backend/indexing/pipeline.py`
- `src/ha_backend/cli.py` (ensure refresh path stays aligned)

Key requirements:

- Indexing and `refresh-snapshot-metadata` must share the same logic path (avoid drift).
- Snippet quality improves per taxonomy without breaking language detection.
- Content selection must degrade gracefully for malformed HTML.

### 3.2 Add `Snapshot.is_archived` (schema + model + extraction)

1) Add Alembic migration adding `snapshots.is_archived` (nullable boolean).
2) Update `src/ha_backend/models.py` `Snapshot` model.
3) Compute `is_archived` at indexing time based on conservative signals:
   - title prefix patterns (e.g., `Archived...`),
   - known bilingual banner phrases in extracted body text (not the UI snippet).

Transition strategy:

- Until backfill is complete, ranking should treat `NULL` as “unknown” and fall back
  to existing title/snippet heuristics (so we don’t remove archived penalty accidentally).

### 3.3 Update FTS vector input (Postgres)

Ensure FTS vectors include:

- Title (high weight),
- Cleaned main content (medium weight; truncated to 4KB),
- URL (low weight),

while the UI snippet remains short.

Plan for backfill:

- `ha-backend backfill-search-vector --force` off-peak, with progress logging.

### 3.4 Tests (focused + regression)

Add unit tests for extraction using representative HTML fixtures.

Assertions should cover:

- Boilerplate pruning removes nav/header/footer style text.
- Content-root selection works when `<main>` is missing.
- Snippets are stable, non-empty, and not dominated by boilerplate.
- Archived detection is conservative and bilingual-aware.

Extend API-level tests to ensure:

- Search results include improved snippet text for seeded examples.
- Archived penalty behavior remains present (via is_archived or heuristic fallback).

**Deliverables:**

- Extraction improvements implemented + tests passing (`make check`)
- Migration applied cleanly in dev/test environments
- Documented refresh/backfill procedure (Phase 7 will finalize canonical docs)

**Exit criteria:** local tests pass; extraction is measurably better on the Phase‑1 examples in local/staging.

---

## Phase 4 — Design: ranking v3 (targeted, explainable, reversible)

Objective: define a small set of ranking changes that improve golden queries without regressions.

### 4.1 Use evidence (not vibes)

From baseline v1/v2 captures:

- Identify where v2 still fails (deep pages outranking hubs, archived pages surfacing too high, etc.).
- Identify whether snippet/vector improvements change recall in ways that may require coefficient retuning.

### 4.2 Define v3 changes (keep it small)

Candidate levers (already present in the codebase in some form):

- Retune query-mode thresholds (`broad` / `mixed` / `specific`) for better blending.
- Retune coefficients in `src/ha_backend/search_ranking.py`.
- Penalties:
  - querystring penalty and tracking penalty (possibly stronger for broad queries),
  - depth penalty tuning (especially for `view=pages` grouping keys).
- Boosts:
  - title token match boost,
  - authority/hubness/pagerank blending when `page_signals` exists.
- Archived penalty:
  - prefer `Snapshot.is_archived` when available and non-NULL (cheaper and more stable than snippet heuristics).

### 4.3 Ensure v3 is explainable via admin debug output

`/api/admin/search-debug` must:

- accept `ranking=v3`,
- show the same score breakdown components,
- remain stable enough for operators to reason about changes.

**Deliverables:**

- A v3 scoring spec: formula components, coefficient table, and per-query-mode blends
- A “v3 target fixes” list mapped to golden queries

**Exit criteria:** v3 spec is narrow, measurable, and reversible.

---

## Phase 5 — Implement + test: ranking v3 + tooling updates

Objective: add v3 without breaking clients; extend tooling to capture/diff v3.

### 5.1 Introduce `v3` everywhere it must exist

Backend surfaces:

- Ranking config + parsing:
  - `src/ha_backend/search_ranking.py`
- Query param validation:
  - `src/ha_backend/api/routes_public.py` (`/api/search`)
  - `src/ha_backend/api/routes_admin.py` (`/api/admin/search-debug`)

Requirements:

- v1 and v2 behavior remains unchanged.
- v3 is opt-in via `ranking=v3` until Phase 7.

### 5.2 Implement v3 scoring logic

Key requirements:

- Works on Postgres (prod) and SQLite (tests/dev).
- Avoids expensive operations unless strictly necessary.
- Uses `Snapshot.is_archived` when present and non-NULL; falls back otherwise.

### 5.3 Update evaluation scripts

Update scripts to allow capturing v3:

- `scripts/search-eval-capture.sh` should accept `--ranking v3`.
- `scripts/search-eval-run.sh` should:
  - support v2 vs v3 comparisons, or
  - support a 3-way capture mode (v1/v2/v3) if useful.

### 5.4 Extend tests

Add test fixtures that demonstrate v2 vs v3 differences aligned to the v3 spec.

Also extend admin search debug tests to validate:

- `ranking=v3` is accepted,
- score breakdown fields remain present and coherent.

**Deliverables:**

- v3 implemented and test-covered
- scripts can capture/diff v3 results

**Exit criteria:** v3 is usable via `ranking=v3`; tests pass.

---

## Phase 6 — Evaluation loop (golden queries) + coefficient tuning

Objective: iterate until v3 reliably improves curated queries without obvious regressions.

### 6.1 Run repeated captures (v2 vs v3)

Capture matrix (minimum):

- `view=pages` + `sort=relevance` (primary)
- `view=snapshots` + `sort=relevance` (debugging)

Store artifacts under:

- `/srv/healtharchive/ops/search-eval`

For each iteration:

- Keep the diff report.
- Add a short note describing:
  - which queries improved,
  - which regressed,
  - what coefficient/heuristic change was made.

### 6.2 Update golden expectations carefully

Update `docs/operations/search-golden-queries.md` only when:

- archive coverage legitimately changed (new sources/pages),
- or expectations were incorrect/outdated.

Do not “move goalposts” to hide ranking regressions.

### 6.3 Performance sanity checks

Ensure v3 does not meaningfully regress production query times.

If it does:

- simplify heuristics,
- ensure expensive joins happen only when needed,
- treat index changes as an explicit subtask (avoid scope creep).

**Deliverables:**

- A short v3 evaluation report section in this plan:
  - what improved,
  - what regressed (if anything),
  - final coefficients and rationale.

**Exit criteria:** v3 is convincingly better on golden queries and not meaningfully slower.

---

## Phase 7 — Production rollout (reversible like v2) + canonical docs updates

Objective: ship v3 safely with clear rollback and updated canonical docs.

### 7.1 Rollout strategy (single VPS)

1) Deploy code with v3 available, keep default at v2.
2) Run production eval with `ranking=v3` explicitly (store capture artifacts).
3) Flip default:
   - set `HA_SEARCH_RANKING_VERSION=v3`,
   - restart API process.

### 7.2 Data maintenance tasks (if required by earlier phases)

Depending on what changed:

- If search vector inputs changed:
  - run `ha-backend backfill-search-vector --force` off-peak.
- If snippet/title extraction changed materially:
  - run `ha-backend refresh-snapshot-metadata --job-id ...` for recent/high-impact jobs first.

### 7.3 Update canonical docs (post-implementation)

- `docs/deployment/search-rollout.md`:
  - add v3 rollout + rollback steps,
  - keep it current and reversible.
- `docs/operations/search-quality.md`:
  - ensure commands mention v3 where relevant.
- `docs/architecture.md`:
  - reflect v3 existence and any new derived fields (e.g., `is_archived`).

### 7.4 Rollback plan

If v3 looks wrong in production:

1) Set `HA_SEARCH_RANKING_VERSION=v2` (or `v1` if needed).
2) Restart API.

Keep `ranking=v3` available for investigation even after rollback.

### 7.5 Close out the plan

When complete:

- Move this plan to `docs/roadmaps/implemented/` with a dated filename.
- Ensure the backlog item is removed/updated in `docs/roadmaps/roadmap.md`.

**Deliverables:**

- v3 live (or a documented “no-go” with evidence)
- canonical docs updated
- this plan archived under `docs/roadmaps/implemented/`

**Exit criteria:** production default is v3 and the golden-query eval is documented as having passed.

---

## Risk register (pre-mortem)

- Risk: snippet cleanup removes signals used for ranking (archived detection).
  - Mitigation: store `Snapshot.is_archived`; keep heuristic fallback for NULL during transition.
- Risk: improved FTS input reshuffles ranking too much.
  - Mitigation: stage changes behind v3; evaluate via captures + diffs.
- Risk: backfills are heavy on a single VPS.
  - Mitigation: batch, off-peak, tmux; prioritize newest jobs first; document stop/resume points.
- Risk: bilingual content behaves poorly with stemming/tokenization.
  - Mitigation: keep `TS_CONFIG='simple'`; include French smoke tests in golden queries.
- Risk: regressions hidden by updated expectations.
- Mitigation: treat `docs/operations/search-golden-queries.md` as a contract; update only when coverage changes.

---

## Phase 1 findings (to be filled after baseline)

Add:

- Baseline run IDs and where artifacts were stored.
- Snippet failure taxonomy summary.
- Top 10 “worst” snippet examples (URL → snippet).
- Any obvious v1 vs v2 relevance observations worth carrying into v3 design.
