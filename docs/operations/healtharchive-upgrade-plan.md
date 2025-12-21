# HealthArchive Upgrade Plan (Agent-Ready)

Status: **active roadmap** (Phases 0-4 implemented; Phase 5 core research exports implemented; Phase 5 releases + Phase 6 pending).

This file is intentionally written so you can hand it to another LLM/AI (or a human contributor) and they will understand:

- What HealthArchive.ca is today (from repo evidence).
- What gaps remain versus a “public-interest service” posture.
- What to build, in what order, and why.
- What constraints and guardrails must not be broken (especially “not medical advice” + security).

## How To Use This Document (for a future agent)

1) **Confirm current state first (don’t guess).** This repo is split across:
   - `healtharchive-frontend/` (Next.js 16 UI on Vercel)
   - `healtharchive-backend/` (FastAPI + worker + indexing + in-tree crawler)
   - Root `ENVIRONMENTS.md` (cross-repo environment matrix / current deployment notes)

2) **Read the canonical docs (high signal).**
   - Cross-repo wiring: `ENVIRONMENTS.md`
   - Backend architecture: `healtharchive-backend/docs/architecture.md`
   - Backend production runbook: `healtharchive-backend/docs/deployment/production-single-vps.md`
   - Annual campaign scope/policy: `healtharchive-backend/docs/operations/annual-campaign.md`
   - Replay runbook: `healtharchive-backend/docs/deployment/replay-service-pywb.md`
   - Replay/preview automation design: `healtharchive-backend/docs/operations/replay-and-preview-automation-plan.md`
   - Frontend implementation guide: `healtharchive-frontend/docs/implementation-guide.md`
   - Frontend deployment verification: `healtharchive-frontend/docs/deployment/verification.md`

3) **Work in small increments.** Each deliverable should be implementable as a small PR:
   - Don’t mix governance copywork with major backend refactors in one change.
   - Keep tests passing (backend `pytest -q`, frontend `npm run lint` + `npm test`).

4) **Non-negotiable safety posture (do not weaken).**
   - HealthArchive is an archive + provenance tool, **not** a guidance provider.
   - Never imply affiliation with government sources.
   - Avoid features that produce medical interpretation (especially AI summaries).
   - Preserve the existing browser hardening: CSP/security headers, iframe sandboxing, strict CORS, admin token gating.

## Current Architecture (as implemented)

### Components

- **Frontend:** `healtharchive-frontend/` (Next.js 16 App Router + TypeScript, Tailwind + `.ha-*` design system)
  - Routes: `/`, `/archive`, `/archive/browse-by-source`, `/snapshot/[id]`, `/browse/[id]`, `/methods`, `/researchers`, `/about`, `/contact`
  - Offline fallback: demo dataset + static HTML stubs under `healtharchive-frontend/public/demo-archive/**` (used when API unreachable / CORS blocked)
- **Backend:** `healtharchive-backend/` (FastAPI + SQLAlchemy + Alembic)
  - Public API endpoints used by the frontend:
    - `GET /api/health`, `GET /api/stats`, `GET /api/sources`, `GET /api/search`, `GET /api/snapshot/{id}`, `GET /api/snapshots/raw/{id}`
    - Replay support: `GET /api/sources/{source}/editions`, `GET /api/replay/resolve`
    - Preview images (optional): `GET /api/sources/{source}/preview?jobId=...`
  - Admin/ops endpoints (must remain off-limits for public UI): `/api/admin/**`, `/metrics`
- **Crawler/orchestrator:** `healtharchive-backend/src/archive_tool/`
  - Drives Docker + Zimit to generate WARCs and job artifacts in a resumable pipeline.
- **Worker:** `healtharchive-backend/src/ha_backend/worker/`
  - Runs queued jobs end-to-end (crawl → index), with retries.
- **Optional replay service:** pywb behind Caddy at `https://replay.healtharchive.ca`
  - Backend generates `browseUrl` values when `HEALTHARCHIVE_REPLAY_BASE_URL` is configured.

### Deployment reality (current posture)

- **Frontend:** Vercel (`https://healtharchive.ca`, `https://www.healtharchive.ca`, plus `https://healtharchive.vercel.app`)
- **Backend API:** Single production API at `https://api.healtharchive.ca` (used for both Preview and Production), with a strict CORS allowlist.
- **Single VPS model:** Postgres + API + worker + storage + Caddy on one server (see `healtharchive-backend/docs/deployment/production-single-vps.md`).

### Key constraints that affect implementation choices

- **CSP/security headers:** Frontend sets a restrictive CSP in report-only mode (`healtharchive-frontend/next.config.ts`).
  - This will constrain “drop-in” third-party scripts for analytics/forms unless explicitly allowed.
- **Strict CORS:** Backend allows only specific frontend origins (intentionally). Branch preview URLs may fall back to demo mode by design.
- **Replay depends on WARCs:** Deleting WARCs breaks replay. Cleanup is intentionally cautious.
- **Single VPS capacity:** Heavy background work (diff generation, replay indexing, preview rendering) must be controlled so it doesn’t harm API responsiveness.

## Terminology (shared language)

- **Source:** A logical origin like “Health Canada” (`hc`) or “PHAC” (`phac`).
- **ArchiveJob (job):** A single crawl run with a config and output directory; after indexing, it becomes an “edition”.
- **Edition:** A user-facing “backup” of a source, typically `job-<id>` (used for replay browsing + edition switching).
- **Snapshot:** A single captured page instance (URL + timestamp + metadata) extracted from WARCs and stored in the DB.
- **Page / page group:** A canonical grouping of multiple snapshots for the “same page” across time, typically keyed by `normalized_url_group` (backend supports `view=pages`).
- **Raw HTML replay:** `GET /api/snapshots/raw/{id}` reconstructs HTML from the WARC for embedding in the frontend.
- **Full-fidelity replay:** pywb replay of the same content (CSS/JS/assets when captured), embedded via `browseUrl`.

## Roadmap Summary (High-Level)

**Ground Truth From The Current Repos (what’s already “upgraded”)**

-   Strong, repeated non-government / non-advice framing already exists in UI copy, especially in the footer and snapshot viewer (`healtharchive-frontend/src/components/layout/Footer.tsx:8`, `healtharchive-frontend/src/app/snapshot/[id]/page.tsx:192`) and a “What this site is/isn’t” block is already on the homepage (`healtharchive-frontend/src/app/page.tsx:191`).
-   The homepage already surfaces live archive metrics via the backend `/api/stats` (with an offline fallback), which is a big “service maturity” signal (`healtharchive-frontend/src/app/page.tsx:13`).
-   Researcher-oriented copy already includes citation guidance and explicitly calls out “compare/timeline/exports” as planned capabilities (`healtharchive-frontend/src/app/researchers/page.tsx:65`).
-   Backend already has: robust search semantics, “pages vs snapshots” view, optional `pages` table fast path, replay integration (pywb), per-source “editions” for switching backups, and ops runbooks + systemd templates for annual scheduling and verification (`healtharchive-backend/docs/architecture.md:22`, `healtharchive-backend/docs/deployment/pages-table-rollout.md:1`, `healtharchive-backend/docs/deployment/systemd/README.md:1`, `healtharchive-backend/docs/operations/annual-campaign.md:1`).
-   Frontend security posture is already deliberate (security headers + CSP in report-only) and will constrain how you add third-party analytics/forms unless you plan it (`healtharchive-frontend/next.config.ts:3`).

Below is a revised, sequential upgrade plan that assumes those realities and avoids duplicating what you already have.

---

## Phase 0 — Tighten Narrative + Reduce Copy Drift (high ROI, mostly editing)

**Goal:** You already have good messaging; this phase makes it _consistent, “research-grade,” and accurate to what’s actually deployed_.

**0.1 Standardize the mission statement (everywhere it matters)**

-   Unify language across: homepage hero, About, Methods, Researchers, metadata description, and any future Governance page.
-   Keep the mission short and “verifiable”: _time-stamped snapshots + auditable changes + citation_, not broad claims.

**Suggested tone (aligned with current copy but sharper):**

-   “HealthArchive.ca preserves time-stamped snapshots of selected Canadian public health web pages so changes remain auditable and citable.”
-   “This is not medical advice and not a substitute for current official guidance.”

**0.2 Make the “archived, not current” message unavoidable on workflow pages**
You already do this well on the homepage and snapshot detail; bring the same clarity to:

-   `/archive` (search is where casual users land)
-   `/browse/[id]` (high-risk page: looks like you’re “showing a site”)

Deliverable concept (no implementation detail): one short, consistent block that appears on Home + Archive + Snapshot + Browse, tuned per context:

-   Archive: “Search historical snapshots. Always verify against the official site for current guidance.”
-   Browse: “You’re browsing an archived capture from <date/time>. Links may not reflect current guidance.”

**0.3 Update Methods text to reflect _what is already real_ (reduce “future tense”)**
Right now Methods reads partially like a conceptual design document (`healtharchive-frontend/src/app/methods/page.tsx:34`). Your backend + production runbook indicates this is already materially implemented.

-   Shift language from “would / intended” → “does / currently,” while keeping the “in development” caveats.
-   Bring in (high-level) the annual edition concept that already exists in ops docs, without exposing infrastructure details.

**0.4 Choose (and explicitly state) primary audiences**
Homepage currently lists clinicians + researchers/journalists + public (`healtharchive-frontend/src/app/page.tsx:104`). That’s fine, but for risk posture you may want:

-   Primary: researchers/journalists/educators
-   Secondary: clinicians/public (with stronger guardrails)
    Deliverable: a subtle re-weighting of copy (not removing audiences, just clarifying intended use).

**Definition of done (Phase 0)**

-   A new visitor landing on `/archive` or `/browse/[id]` cannot miss “archived, not current guidance; not medical advice; independent project.”
-   Methods/About/Researchers copy matches the actual deployed architecture and policies (annual editions, limited scope, optional replay).

---

## Phase 1 — Public Governance Layer (ABS-critical; mostly writing + lightweight process)

**Goal:** Convert your already-solid engineering into “public-interest infrastructure” with clear rules.

**1.1 Publish a Governance section (public-facing)**
You already have deep internal ops docs; this phase turns the right parts into a stable, public policy surface.
Include (plain language, not legalese):

-   Mission + scope boundaries (what you archive, what you intentionally don’t)
-   Source inclusion criteria (why a source is in/out)
-   Provenance commitments (what metadata you guarantee; how you label captures)
-   Corrections policy (what counts as a “correction,” expected response time)
-   Takedown/opt-out policy (including how you handle edge cases and third-party content)
-   Non-affiliation / non-authoritativeness statement (already strong in footer; reference it consistently)

**1.2 Terms + Privacy pages**
You currently have no explicit terms/privacy pages in the frontend route structure.
Keep them short and specific:

-   “No accounts; no patient info collected”
-   What telemetry exists (today it appears “none”; if you add analytics later, this page becomes the contract)
-   Content use posture (research/education; not medical advice; link to official sources)

**1.3 Public Changelog**
Your repos will naturally evolve; a changelog makes “maintenance over time” legible.

-   Monthly cadence is enough.
-   Content examples: “Added source CIHR to annual scope,” “Improved replay edition switching,” “Search improvements,” “Incident resolved.”

**1.4 “Report an issue” intake that’s more structured than “email us”**
Contact already invites issue reports (`healtharchive-frontend/src/app/contact/page.tsx:32`), but the process isn’t explicit.
Deliverable concept:

-   A clearly labeled “Report an issue” entry point (footer and/or viewer UI).
-   A simple set of categories + what happens next + response expectations.

**1.5 Advisory circle (process, not code)**
Your internal documentation quality is already high; an advisory circle makes it externally defensible.

-   Aim for 2–4 people; publish a charter and cadence.
-   Keep meeting notes minimal and policy-focused (scope, risk posture, corrections).

**Definition of done (Phase 1)**

-   The site has publicly visible: Governance, Terms, Privacy, Changelog, Report-an-issue flow.
-   A neutral reviewer can understand your rules and safety posture in under 2 minutes.

### Phase 1 Implementation Plan (Detailed; sub-phases)

This phase is deliberately **mostly writing + lightweight UI + lightweight backend plumbing**. The goal is not bureaucracy — it’s to make HealthArchive legible, defensible, and easy to verify as “public-interest infrastructure”.

**Design principles (Phase 1)**

- **Plain language over legalese.** You are defining procedures and expectations, not trying to replace a lawyer.
- **Minimal collection.** No accounts, no tracking IDs tied to people, no sensitive submissions. Default to “do not submit personal/health information.”
- **Consistency.** The same core “this is an archive / not guidance / not medical advice” language should appear across the new pages, reusing frontend’s canonical copy source (`healtharchive-frontend/src/lib/siteCopy.ts`).
- **Operational reality.** Policies should match how you actually operate today (annual editions, constrained scope, optional replay).
- **Fail safe.** When the API is unreachable, reporting should still work (e.g., mailto fallback).

#### Sub-phase 1A — Inventory + decisions (½ day)

**Goal:** Avoid writing policies that contradict the real system or overpromise.

Checklist:

- Confirm what is already stated on `/methods`, `/about`, `/researchers`, footer disclaimers, and Phase 0 callouts.
- Confirm what user data is currently collected (likely: server access logs via Caddy/uvicorn; no explicit frontend analytics).
- Decide the **minimum policy commitments** you are comfortable operationalizing:
  - Corrections response time (example: “within 7 days”; urgent labeling issues: “within 48 hours”).
  - What you will and won’t take down (government sources vs third-party; link-outs vs full removal; “restrict access” option).
  - Which contact channel is authoritative (email; optionally GitHub issues for technical items).
- Decide the initial “advisory circle” stance:
  - If you don’t yet have advisors, plan to publish “seeking advisors” language rather than faking a board.

Deliverables:

- A short “Phase 1 policy decisions” note (can live as a section in the changelog or as internal notes).
- A list of new public routes to create (see Sub-phase 1C).

#### Sub-phase 1B — Draft public Governance content (1–2 days)

**Goal:** Ship a governance page that answers the big questions quickly.

Governance page structure (recommended single page with anchored sections):

1) **Mission + audience**
   - One sentence mission (align with Phase 0).
   - Primary audiences (researchers/journalists/educators) + secondary audiences (public/clinicians) with guardrails.

2) **Scope + inclusion criteria**
   - What sources qualify (Canadian public health agencies; criteria examples):
     - Publicly accessible pages
     - High-impact guidance/data/communications
     - Stable provenance labeling possible
   - What’s out of scope (examples to explicitly state):
     - Private/internal content; anything behind login
     - User-submitted or personal data sources
     - Any attempt to “mirror the entire internet”
   - “Reliability over breadth” statement.

3) **Provenance commitments**
   - What you guarantee to show on snapshot pages (examples):
     - Capture timestamp (timezone)
     - Source name/code
     - Original URL
     - Snapshot permalink
   - Explicit limitation examples:
     - Some JS dashboards may not replay perfectly
     - Missing assets may occur
     - Captures represent “what the crawler saw”, not a perfect reconstruction

4) **Corrections policy**
   - What counts as a correction:
     - Wrong metadata, broken replay/raw HTML, mislabeled source, missing warnings
   - What does *not* count as a correction:
     - Disagreements with what an agency published
   - Response expectations (SLA language you can meet)
   - How corrections are documented (ties to changelog; optional per-snapshot note later)

5) **Takedown / opt-out policy**
   - Most content is from government sources; still define:
     - How to request review
     - What you do when a request is credible (e.g., restrict access while reviewing)
     - How you handle third-party embedded content captured inside a government page
   - Make it explicit you don’t promise removal of public-interest government material unless there’s a compelling reason.

6) **Non-affiliation + “not medical advice”**
   - Reference footer disclaimers.
   - Make the “what this is / isn’t” block visible.

7) **Advisory circle**
   - Charter summary and cadence.
   - If not yet formed: “seeking advisors” + what backgrounds you’re looking for.

Examples to include (short, non-interpretive):

- “Researchers: cite what was visible on Jan 01, 2025.”
- “Journalists: track when wording on a guidance page changed.”

Deliverables:

- Draft Governance copy ready for implementation as a new public route.

Acceptance criteria:

- A first-time visitor can read only the headings and understand: purpose, scope, provenance, corrections, takedown, and non-advice posture.

#### Sub-phase 1C — Add public pages and navigation (frontend) (1–2 days)

**Goal:** Make governance + policies discoverable without cluttering the primary nav.

Recommended new frontend routes:

- `/governance` (main page; anchored sections)
- `/terms`
- `/privacy`
- `/changelog`
- `/report` (or `/report-issue`)

Navigation/linking strategy:

- Add links in the **footer** (preferred) under a small “Project” or “Policies” column.
- Keep the header nav unchanged for now (avoid overwhelming top-level IA).
- Ensure each of these pages repeats the core disclaimers (reuse canonical copy + the existing footer).

Changelog page content model (choose one):

- **Option A (simplest):** A Markdown file committed in the frontend repo and rendered by the page.
- **Option B:** A lightweight JSON/YAML content file with date/title/body entries.

Deliverables:

- Public pages implemented with consistent typography and accessible structure.
- Footer links added.

Acceptance criteria:

- All pages build on Vercel, pass lint/tests, and do not introduce third-party scripts or weaken CSP.

#### Sub-phase 1D — “Report an issue” intake UX (frontend) (1 day)

**Goal:** Structured intake without collecting sensitive info.

Form fields (recommended):

- Category (dropdown):
  - Broken snapshot/replay
  - Incorrect metadata (date/source/URL)
  - Missing snapshot / request a capture
  - Takedown / content concern
  - General feedback
- Optional context:
  - Snapshot ID (if applicable)
  - Original URL (if known)
  - Description (required; include “Do not include personal/health info” warning)
- Optional contact email (explicitly optional; users can also just email `contact@healtharchive.ca`)

Failover behavior:

- If backend API is reachable: submit the form and return a short “received” confirmation with a reference ID.
- If backend API is unreachable: provide a “mailto” fallback that pre-fills subject/body with the selected category + details.

Deliverables:

- A clear “Report an issue” page that explains what happens next and expected response times.

Acceptance criteria:

- A user can report an issue even if the live API is down (offline fallback still works).

#### Sub-phase 1E — Minimal backend support for issue intake (1–2 days)

**Goal:** Make reports actionable and auditable, without creating a large moderation system.

Recommended backend capabilities:

- A small DB-backed “issue report” record with:
  - category, description, optional snapshot_id, optional original_url, created_at
  - optional reporter_email (nullable)
  - status (new / triaged / resolved)
  - internal notes (admin-only)
- A **public** POST endpoint for submissions (with input validation and spam protection).
- An **admin-only** endpoint to list and view reports, protected by existing admin token rules.

Spam/risk controls (pick a minimal set you can sustain):

- Rate limit by IP (coarse) and/or a “honey pot” hidden field.
- Hard cap payload sizes.
- Explicitly reject submissions that look like they include personal health information (at minimum, a warning plus optional keyword heuristics).

Deliverables:

- Issue intake pipeline exists end-to-end (frontend → backend → admin view).

Acceptance criteria:

- Public submissions work from production origin(s) without weakening CORS.
- Admin token remains required for browsing report details.

#### Sub-phase 1F — Advisory circle (non-code) (ongoing; start in Phase 1)

**Goal:** Create real external credibility.

Steps:

- Draft a 1-page advisory charter:
  - Purpose (scope/risk/governance review, not operations)
  - Cadence (quarterly)
  - What advisors do and don’t do
- Identify and contact candidates:
  - librarian/archivist, public health researcher, science communication/journalism
- Publish either:
  - Names/titles (with permission), or
  - A “seeking advisors” section until you have consent to publish names

Deliverables:

- Charter text included on `/governance` (or linked from it).

#### Sub-phase 1G — Update docs and prove maintenance (½ day)

**Goal:** Make Phase 1 changes easy for a future maintainer to understand.

- Update:
  - Frontend docs index or implementation guide (where the new routes live)
  - Backend docs index (new issue intake endpoints, if added)
- Add a “Phase 1 complete” entry to `/changelog` with date + bullet list.

**Definition of done (Phase 1, detailed)**

- New public pages exist and are linked from the site: `/governance`, `/terms`, `/privacy`, `/changelog`, `/report` (or equivalent).
- Policies describe how HealthArchive actually operates today, including annual editions, constrained scope, and replay limitations.
- Issue reporting is structured and works even when the API is down (via fallback).
- No new sensitive data is collected; privacy page matches reality.
- All tests pass (frontend + backend) and CSP/CORS/admin protections are not weakened.

**Status (Phase 1 implementation)**

- Implemented on 2025-12-21.
- Public pages added: `/governance`, `/terms`, `/privacy`, `/changelog`, `/report`.
- Issue intake pipeline implemented with a backend `/api/reports` endpoint and admin views.
- Frontend uses a same-origin proxy route (`/api/report`) so reporting works even when the backend CORS policy remains strict.
- Footer and snapshot/browse views link to the report flow.

--- 

## Phase 2 — Make Impact Measurable + Visible (build on what you already have)

**Goal:** You already show “snapshots/pages” on the homepage; now add the metrics that prove reliability and adoption.

**2.1 Define the official metric set (small, stable, defensible)**
You already have “Archived snapshots” and “Unique pages” via `/api/stats`. Add (conceptually):

-   Coverage: sources tracked + per-source coverage window
-   Freshness: time from capture completion → searchable
-   Reliability: crawl success rate, indexing success rate, API uptime
-   Usage: searches/day, snapshot views/day, browse views/day (requires analytics or backend logging strategy)
-   Engagement: digest subscribers (once Phase 3 exists)
-   External validation: partner links/embeds, citations/mentions

**2.2 Add a public Status/Metrics page**
This shouldn’t be “vanity metrics”; it should make the service look professionally operated.
Suggested sections:

-   “Current status”: API health, last successful capture per source, replay availability (if enabled)
-   “Coverage”: sources tracked; first/last capture dates per source (already available via `/api/sources`)
-   “Reliability”: 30/90-day uptime + recent incidents (even manual initially)
-   “Data notes”: known limitations; what “missing” means

**2.3 Add analytics deliberately (or explicitly don’t)**
Right now there’s no analytics code in the frontend. Decide:

-   If you add analytics, pick a privacy-preserving approach and align CSP/security headers accordingly (`healtharchive-frontend/next.config.ts:3`).
-   If you do not, say so explicitly in Privacy, and rely on server-side aggregate counts + partner evidence.

**2.4 Monthly impact report artifact**
Make this a repeatable, boring discipline:

-   “What changed in the archive”
-   “Reliability improvements”
-   “Coverage changes”
-   “Usage snapshot”
-   “Partner highlights”
    This becomes your ongoing “proof file.”

**Definition of done (Phase 2)**

-   You can point to a stable public page that answers: “Is it up? what’s covered? how current? is it being used?”
-   You can produce (even manually) a monthly impact report with consistent fields.

---

## Phase 3 — Change Tracking + Compare + Digest (major new capability; biggest adoption unlock)

**Goal:** You already have search + snapshots + replay editions. This phase adds the missing “what changed?” layer while staying non-interpretive.

**3.1 Define “change tracking” in a strictly descriptive way**
Guardrail language (recommend adopting everywhere this appears):

-   “We report _textual changes_ between archived captures. We do not interpret or recommend actions.”

Decisions to make up front (policy-level, not code):

-   What counts as a “meaningful change” vs boilerplate
-   How to handle noisy pages (dashboards, frequently regenerated pages)
-   Whether you support per-page timelines for all pages or only “high-signal” pages first

**3.2 Compare view**
User-facing outcomes:

-   “Compare two versions” from snapshot detail (and/or from a page timeline)
-   Highlight changed sections; show “added/removed/changed” counts
-   A clear “context” strip: source, original URL, capture timestamps, edition/job

**3.3 “What changed” feed**
A new surface that drives repeat visits:

-   “Changed this week” feed
-   Filterable by source and date
-   Optional curated topic groupings later (be careful: topic tagging can imply editorial authority; keep it mechanical if possible)

**3.4 Digest (start web + RSS; email later)**
Given your CSP/security posture, start with:

-   Web digest archive page
-   RSS feed
    Add email only after you’re confident in content quality and cadence.

Digest categories (aligned with your current project goals):

-   Top changes (by magnitude/importance heuristic, explained plainly)
-   New pages discovered
-   Pages removed/redirected (as observed via crawl)
-   “Coverage notes” (e.g., a source had capture issues)

**Definition of done (Phase 3)**

-   A user can answer: “What changed on PHAC pages last week?” without manual searching.
-   Compare output is descriptive, provenance-rich, and does not read like guidance.

---

## Phase 4 — Distribution + External Validation (mostly non-code, but critical)

**Goal:** Turn “useful site” into “verified public service.”

**4.1 Partner target list + pitch assets**
You already have strong narrative copy; formalize it into partner-ready material:

-   1-page brief (mission, safety posture, screenshots, metrics) — now published at `/brief`
-   Citation guidance (snapshots + compare views) — now published at `/cite`
-   Example compare + digest pages — now published at `/compare` and `/digest`

**4.2 Secure one distribution partner + one verifier**
Treat them as different roles:

-   Distribution partner: links/embeds/shares digest
-   Verifier: can credibly confirm you built/operate it and it’s used

**4.3 Partner-facing “embed” surface (keep it simple)**
Start with the lowest-friction distribution format:

-   “Recent changes for <source>” widget and/or RSS feeds per source
    Keep this lightweight so you don’t create a new support burden.

**Definition of done (Phase 4)**

-   One external org publicly links/embeds; one named verifier is willing to attest to use and impact; you can show basic usage metrics.

---

## Phase 5 — Research-Grade Outputs (you’re already halfway there)

**Goal:** Your backend is already closer to “research API” than most projects; now package it safely.

**5.1 Formal citation guidance**
Citation guidance is now published at `/cite`, but Phase 5 should make it harder to misuse and easier to reuse:

-   Ensure the recommended archived URL format corresponds to what production serves.
-   Include capture timestamp (with timezone) and original URL.
-   Consider adding “how to cite when replay is enabled vs raw HTML only” (conceptually).

**5.2 Research access pathway**
Two tracks:

-   Human: clear documentation, limitations, and contact path for bulk needs.
-   Machine: stable exports for snapshot metadata and (later) change events.
    Keep it “small and safe” first (metadata only), and make sustainability explicit (rate limits, fair use).

**5.3 Scholarly output**
Once Phase 3 exists, you have publishable material:

-   A methods note/poster on provenance + change tracking without interpretation
-   A small descriptive analysis: “guidance drift” patterns over time (careful framing)

**Definition of done (Phase 5)**

-   A researcher can cite snapshots correctly and request or retrieve structured metadata and change-event data without bespoke coordination.
    See the expanded Phase 5 implementation plan later in this document for sub-phases and acceptance criteria.

---

## Phase 6 — Reliability + Sustainability (a lot already exists; now operationalize it)

**Goal:** You already have serious ops docs and automation templates; this phase turns them into a lived routine and a public-facing posture.

**6.1 Publish capture cadence policy (public)**
Internally you already have the annual campaign definition and scope rules (`healtharchive-backend/docs/operations/annual-campaign.md:1`).
Publicly, you want:

-   Annual edition concept (Jan 01 UTC)
-   What triggers ad-hoc captures (rare, explicit)
-   Why scope is limited (reliability > breadth)

**6.2 Formalize ops cadence (internal)**
You already have checklists and systemd templates (`healtharchive-backend/docs/deployment/systemd/README.md:1`).
Make the “boring routine” explicit:

-   Weekly health review
-   Monthly reliability review (ties into the impact report)
-   Quarterly restore test
-   Dependency patch cadence

**6.3 Growth constraints**
You already have a single-VPS production runbook (`healtharchive-backend/docs/deployment/production-single-vps.md:1`) and strict CORS design.
Define explicit constraints to avoid scope creep:

-   Source cap for the year
-   Storage budget and retention posture (especially with replay depending on WARCs)
-   Performance budgets (API latency, indexing time)

**Definition of done (Phase 6)**

-   You have a documented and practiced operational routine, plus a public statement of cadence/scope constraints.

---

## The Updated “If You Only Do 6 Things” (highest ROI given what’s already built)

1. Public Governance + Terms/Privacy + Corrections/Takedown process
2. Public Changelog + monthly impact report discipline
3. Public Status/Metrics page (build on existing `/api/stats` homepage metrics)
4. Change tracking + Compare + “What changed” feed (strictly descriptive)
5. Digest (web + RSS first) + subscriber metric once email is added
6. One distribution partner + one authoritative verifier (non-code, but decisive)

---

## Expanded Guidance (Copious Context + Examples)

This section “adds meat to the bones” so an implementation agent can translate the roadmap into concrete, incremental tasks without reinventing the rationale.

### Non-negotiable project posture (repeat in every implementation)

HealthArchive must always read as:

- **Independent** (not government, not endorsed, not affiliated)
- **Archival** (historical record, time-stamped)
- **Non-authoritative** (not current guidance; not medical advice)
- **Reproducibility-first** (citations point to what was visible on date X)
- **Safety-first** (avoid features that look like interpretation or advice)

If a new feature increases the risk of misinterpretation (e.g., “summaries,” “key takeaways,” “what it means”), it should be treated as out-of-scope unless it is purely descriptive and has strong guardrails.

### A note on “ABS-style” impact framing (why these upgrades matter)

The upgrade plan is intentionally not “more code for the sake of code.” It’s about converting a technically solid archive into:

- A governed public-interest service (clear rules, corrections, takedown posture)
- A measurable service (metrics, reliability signals, visible operational maturity)
- A repeat-usage product (change tracking + digest)
- A verifiable project (external partners + credible verifier)

In other words: **institutionalization + proof of use**, not just implementation.

---

## Phase 0 — Tighten Narrative + Reduce Copy Drift (expanded)

### Why this matters

- HealthArchive already has strong disclaimers, but they’re not uniformly present on the highest-traffic workflow pages (search/browse).
- A consistent “what this is/isn’t” block reduces:
  - confusion (“is this current guidance?”),
  - reputational risk (“are you speaking for PHAC?”),
  - and legal/compliance ambiguity (“are you collecting data?”).
- Consistent copy also reduces future maintenance cost: you update one canonical block, not five divergent variations.

### What already exists (repo evidence)

- Footer disclaimers: independence + non-advice (`healtharchive-frontend/src/components/layout/Footer.tsx`).
- Homepage “What this site is/isn’t” block (`healtharchive-frontend/src/app/page.tsx`).
- Snapshot viewer “Important note” block (`healtharchive-frontend/src/app/snapshot/[id]/page.tsx`).
- About and Methods pages already emphasize independence and non-partisanship (`healtharchive-frontend/src/app/about/page.tsx`, `healtharchive-frontend/src/app/methods/page.tsx`).

### Main gaps to close

- `/archive` currently emphasizes search UX and offline fallback; it doesn’t prominently restate the archive/non-advice posture where users make decisions about content.
- `/browse/[id]` does a good job technically, but it’s still the highest-risk page for misinterpretation because it looks like “the website” (not a record).
- Methods page is written partly in “future tense,” even though the backend and runbook show much of this is real.

### Deliverables (what to produce)

1) A **single canonical mission block** (2–3 sentences) used consistently across:
   - `/` (home)
   - `/about`
   - `/methods`
   - `/researchers`
   - metadata description in `healtharchive-frontend/src/app/layout.tsx`

2) A **single canonical “What this is/isn’t” block** surfaced on:
   - `/` (already exists; may tighten language)
   - `/archive` (add)
   - `/snapshot/[id]` (already has similar; align wording)
   - `/browse/[id]` (add a more prominent version)

3) Methods copy updated from “conceptual design” → “current reality + limitations.”

### Copy examples (ready-to-adapt)

Mission (2–3 sentences):

> HealthArchive.ca preserves time-stamped snapshots of selected Canadian public health web pages so changes remain auditable and citable.  
> It is an independent, non-governmental archival project — not medical advice and not a substitute for current official guidance.

What this is / isn’t (workflow-safe):

> **This is:** an archival record of what public health websites displayed at a specific time, with capture dates and citations.  
> **This is not:** current guidance, medical advice, or an official government website.  
> **For current recommendations:** always consult the official source website.

Browse-mode warning (short, unavoidable):

> You are viewing an archived capture from **<capture date/time>**. Links and content may be outdated or superseded. For current guidance, use the official website.

### Acceptance criteria (practical)

- A user can enter via `/archive` or a shared `/browse/[id]` link and still see an explicit, plain-language disclaimer without scrolling.
- Copy on Methods/About/Researchers does not imply “planned someday” for already-live infrastructure (but still clearly marks the project as “in development” and acknowledges limitations).

### Phase 0 implementation plan (highly detailed; sub-phases)

This plan is written so another agent can implement Phase 0 as a sequence of small, low-risk PRs. The intent is to improve **copy consistency**, **risk posture**, and **accuracy** without adding new product features.

#### Phase 0A — Inventory and decide the “canonical copy”

**Purpose**

- Reduce copy drift across pages.
- Avoid accidental “future tense” descriptions that contradict current backend reality.
- Ensure the “archived/not current” message appears on high-risk entrypoints (`/archive`, `/browse/[id]`) as well as home/snapshot.

**Tasks**

1) Inventory all existing disclaimers and mission-like sentences in the frontend:
   - Footer independence and “not medical advice” language.
   - Homepage hero description.
   - Homepage “What this site is/isn’t” block.
   - Snapshot viewer “Important note.”
   - About/Methods/Researchers intro copy.
   - Document metadata description (`healtharchive-frontend/src/app/layout.tsx`).
2) Identify any copy conflicts or drift:
   - Places where “intended to” / “would” is used despite current implementation.
   - Places where the disclaimer is missing entirely on workflow pages.
   - Inconsistent phrasing that could be quoted out of context (e.g., “public health information” vs “public health guidance” vs “webpages”).
3) Decide the canonical text for:
   - **Mission block** (2–3 sentences).
   - **What this is / isn’t** (3 short bullets or 3 lines).
   - **Browse-mode warning** (1 short line suitable for “above the fold”).

**Deliverables**

- A final, agreed “canonical copy” snippet that will be reused across the site.
- A short note (1–2 paragraphs) explaining the rationale and any intentional wording choices (e.g., why “web pages” vs “guidance”).

**Recommended canonical text (starting point; edit as needed)**

- Mission (short):
  - “HealthArchive.ca preserves time-stamped snapshots of selected Canadian public health web pages so changes remain auditable and citable.”
  - “It is an independent, non-governmental archival project — not medical advice and not a substitute for current official guidance.”
- What this is / isn’t:
  - “This is: an archival record of what public websites displayed at a specific time, with capture dates and citations.”
  - “This is not: current guidance, medical advice, or an official government website.”
  - “For current recommendations: consult the official source website.”
- Browse warning:
  - “You are viewing an archived capture from <capture date/time> — not current guidance.”

**Acceptance criteria**

- There is exactly one “canonical” version of each copy block, and any variations are intentional and documented.

#### Phase 0B — Create a reuse mechanism (to prevent drift)

**Purpose**

- Make it hard for future edits to accidentally diverge.
- Ensure the same message appears consistently across multiple routes without manual copy-paste.

**Recommended approach (choose one)**

- Option 1 (preferred): create a small shared UI component (or two) that renders:
  - Mission block (short)
  - What this is/isn’t block (compact)
  - Browse warning (ultra-compact)
- Option 2: create a single exported “copy constants” object used by page components.

**Decision criteria**

- If the block has layout/structure (headings, list) → component is usually better.
- If it’s a single sentence reused in metadata and in-page copy → constants can reduce duplication.

**Deliverables**

- A single source of truth for the canonical copy, referenced by:
  - `/` (homepage)
  - `/archive`
  - `/snapshot/[id]`
  - `/browse/[id]`
  - and optionally metadata description (or a close paraphrase, if metadata needs to be shorter).

**Acceptance criteria**

- Future contributors can update the canonical disclaimer in one place.
- No page introduces new disclaimer phrasing unless it’s a deliberate exception.

#### Phase 0C — Update workflow entrypoints (highest risk pages)

**Purpose**

- Users often enter the site via:
  - `/archive` (search/browse),
  - `/snapshot/[id]` (shared links),
  - `/browse/[id]` (embedded archived page).
- These are also where misinterpretation risk is highest.

**Tasks**

1) `/archive`:
   - Add a compact “archived/not current guidance” block near the top of the page (above search/results), tuned to browsing/search context.
   - Ensure it remains visible in both “live API” and “offline fallback” modes.
2) `/browse/[id]`:
   - Add a short warning above the iframe and keep it “above fold” on typical laptop/mobile widths.
   - Include capture date/time in the message when available (this is already present in the browse header UI; the warning should explicitly use it).
3) `/snapshot/[id]`:
   - Align existing “Important note” wording with the canonical text (don’t remove detail; just remove drift).
   - Ensure provenance fields displayed (source, capture date, original URL) stay prominent and readable.

**Acceptance criteria**

- A user landing directly on `/archive` or `/browse/[id]` sees the “archived/not current” message without scrolling.
- The message does not imply medical interpretation or government endorsement.

#### Phase 0D — Normalize “about/methods/researchers” to current reality

**Purpose**

- These pages frame the project; they must be accurate to the implemented stack and operational posture.
- The goal is to remove unnecessary “future tense” while preserving honest “in development” status and limitations.

**Tasks**

1) `/methods`:
   - Replace “intended to” / “would rely on” language where the backend already does the thing today (WARC storage, indexing, replay options).
   - Add a short, high-level statement about the annual edition concept (Jan 01 UTC) as the default cadence, and explicitly state scope is constrained by reliability.
2) `/about`:
   - Keep the motivation and non-partisanship stance.
   - Add one sentence linking “why the archive exists” to research/journalism reproducibility (“citable snapshots,” “auditability”).
3) `/researchers`:
   - Keep current “planned capabilities” callout but make sure it matches the roadmap (timeline/compare/exports/diffs).
   - Ensure citation guidance matches the site’s actual URL patterns and the snapshot viewer’s semantics (replay vs raw HTML).

**Acceptance criteria**

- These pages do not contradict the backend runbooks (annual campaign, single VPS reality, optional replay).
- These pages do not overpromise completeness or fidelity.

#### Phase 0E — Metadata + consistency checks (finish work)

**Purpose**

- Reduce the chance that the site is indexed or quoted with misleading metadata.
- Ensure site-wide consistency and accessibility of the disclaimers.

**Tasks**

1) Metadata description (`healtharchive-frontend/src/app/layout.tsx`):
   - Ensure it includes independence + archive + “what it was at the time” framing.
   - Keep it short enough for search engine snippets.
2) Consistency pass:
   - Confirm the same canonical disclaimer appears on:
     - Home, Archive, Snapshot, Browse.
   - Confirm Terms/Privacy/Governance are not referenced yet (Phase 1 will add them), unless you explicitly want “coming soon” links.
3) Accessibility pass:
   - Ensure the disclaimer blocks use readable text sizes and don’t rely on color alone.
   - Ensure any new callouts have appropriate semantics (e.g., headings, lists).
4) “Out of scope” confirmation:
   - Confirm Phase 0 did not introduce any new backend endpoints, analytics scripts, or data collection.

**Acceptance criteria**

- Copy is consistent, accurate to current deployment, and visible where it matters.
- No security posture regressions (CSP, iframe sandboxing, strict “public API only” usage).

#### Suggested PR breakdown (sequencing)

To keep changes small and reviewable:

1) PR-0: Add canonical copy mechanism (component/constants) + update homepage to use it.
2) PR-1: Add disclaimer block to `/archive`.
3) PR-2: Add browse-mode warning to `/browse/[id]` (and align snapshot page wording).
4) PR-3: Update `/methods`, `/about`, `/researchers` to match current reality and the canonical copy.
5) PR-4: Metadata + final consistency/accessibility pass.

#### Phase 0 “Definition of done” (checklist)

- Canonical mission + “is/isn’t” + browse warning copy exists in one place and is reused.
- `/archive` and `/browse/[id]` show “archived/not current guidance” without scrolling.
- Methods/About/Researchers copy no longer reads like a hypothetical system when it is already deployed.
- No new tracking/analytics added (Phase 2 will decide this deliberately).

#### Phase 0 implementation notes (completed)

The following Phase 0 items are implemented in the current repo state:

- Canonical copy source of truth: `healtharchive-frontend/src/lib/siteCopy.ts`
  - Used for metadata description: `healtharchive-frontend/src/app/layout.tsx`
  - Used on the homepage “is/isn’t” block: `healtharchive-frontend/src/app/page.tsx`
- Workflow disclaimers added/normalized:
  - `/archive` callout: `healtharchive-frontend/src/app/archive/page.tsx`
  - `/archive/browse-by-source` callout: `healtharchive-frontend/src/app/archive/browse-by-source/page.tsx`
  - `/browse/[id]` warning with capture date and “not current guidance or medical advice”: `healtharchive-frontend/src/components/replay/BrowseReplayClient.tsx`
  - `/snapshot/[id]` “Important note” aligned to canonical language: `healtharchive-frontend/src/app/snapshot/[id]/page.tsx`
- “Future tense” reduction / accuracy updates:
  - About: `healtharchive-frontend/src/app/about/page.tsx`
  - Methods (capture pipeline + annual edition posture described as policy): `healtharchive-frontend/src/app/methods/page.tsx`
  - Researchers citation guidance updated to match live numeric snapshot URLs: `healtharchive-frontend/src/app/researchers/page.tsx`
- Confirmed non-goals for Phase 0: no new analytics scripts, no new backend endpoints, no changes to CSP/CORS/iframe sandboxing.

---

## Phase 1 — Public Governance Layer (expanded)

### Why this matters

Governance is the difference between:

- “a cool archive site” and
- “a public-interest archival service that others can safely rely on.”

It also reduces risk by making your policies explicit before anyone asks (and before you scale).

### What already exists

- Internal operational and architecture documentation is already unusually strong in `healtharchive-backend/docs/**`.
- Public-facing pages cover motivation and limitations but do not yet provide:
  - correction procedure,
  - takedown/opt-out posture,
  - a formal scope/inclusion policy,
  - or Terms/Privacy as explicit pages.

### Deliverables (public, non-code-first)

These are best implemented as simple public pages in the frontend (static content first; automation later).

1) **Governance page** (new public route; content-first)
   - What the project is / isn’t (canonical block)
   - Scope boundaries (what sources count; what’s out of scope; why)
   - Source inclusion criteria (mechanical, not vibes)
   - Provenance commitments (what metadata you guarantee on snapshots)
   - Corrections policy (what can be corrected and typical response times)
   - Takedown/opt-out policy (how to request, how decisions are made, how you handle third-party content)
   - Contact / escalation path

2) **Terms page**
   - Research/reference use, no medical reliance
   - No endorsement / no affiliation
   - Copyright/takedown posture (plain language; do not overclaim legal certainty)

3) **Privacy page**
   - Explicit “no patient data / no accounts”
   - What logs/analytics exist today
   - If analytics are added later, this page becomes the contract; update it in lockstep.

4) **Changelog page**
   - Monthly cadence is fine.
   - Include “what changed” across: scope, features, reliability, policy.

5) **Report an issue page (intake)**
   - A structured way to report: broken snapshot, wrong metadata, replay failure, missing page, correction request, takedown request, source suggestion.
   - Explain what information helps you triage (snapshot ID, original URL, screenshot, time, browser).

### Examples: policy language (plain, defensible)

Corrections:

> **Corrections:** If metadata is wrong (capture date, source labeling, broken link) or a snapshot fails to load, report it. We aim to acknowledge reports within **7 days**, and urgent safety labeling issues within **48 hours**. Not all issues are fixable (some depend on what the crawl captured), but we document limitations and outcomes.

Takedown / opt-out:

> **Takedown requests:** If you are a site owner or rights holder and believe content should not be displayed, contact us with the URL(s) and your rationale. We review requests in good faith and may remove access, limit distribution, or add context. We publish aggregate counts of takedown requests in our transparency reporting.

### Acceptance criteria

- A neutral user can find Governance/Terms/Privacy in under two clicks from any page (header or footer).
- The governance pages do not introduce new risk (no promises you can’t keep; no legal overreach; no secrets).

---

## Phase 2 — Make Impact Measurable + Visible (expanded)

### Why this matters

- You already expose “snapshots/pages” via `/api/stats` and show them on the homepage. That’s a strong start.
- The next step is making reliability and coverage legible in a “service-like” way:
  - last capture per source,
  - uptime posture,
  - known issues,
  - and (if you choose) usage metrics.

### What already exists

- Backend has public endpoints that can power a status/metrics page without admin access:
  - `/api/health` (basic status + DB + counts)
  - `/api/stats` (snapshots/pages/sources totals)
  - `/api/sources` (per-source record counts + first/last capture dates + optional entry points and preview URLs)
- Frontend already uses `/api/stats` live on the homepage with fallback (`healtharchive-frontend/src/app/page.tsx`).
- Ops docs already recommend external uptime checks (`healtharchive-backend/docs/operations/monitoring-and-ci-checklist.md`).

### Deliverables

1) **Public status/metrics page** (new route)
   - Keep it simple and honest; don’t claim full observability if you don’t have it.
   - Prefer metrics you can compute from existing public API responses.

2) **Metric definitions (public or semi-public)**
   - A short, explicit definition of each metric you report (so it’s not “numbers with vibes”).

3) **Monthly impact report template**
   - A one-page, repeatable artifact you can publish (web post or PDF).

4) **Analytics decision**
   - Decide: no analytics (privacy-first) vs privacy-preserving analytics vs server-side aggregates.
   - Important: frontend CSP is restrictive; adding third-party scripts is a deliberate security decision, not a “drop-in.”

### Suggested metrics (with precise definitions)

- **Coverage**
  - `sourcesTotal`: number of sources visible in `/api/sources`.
  - `pagesTotal`: unique page groups (from `/api/stats`).
  - `snapshotsTotal`: total snapshots (from `/api/stats`).
- **Freshness**
  - per source: “last capture date” from `/api/sources` (this is a proxy for freshness; “time-to-index” requires additional tracking).
- **Reliability**
  - API uptime: measured via external monitor of `/api/health` + frontend `/archive`.
  - Crawl/index success rate: requires tracking job outcomes; can be public later as aggregates.
- **Usage**
  - Only if you choose to measure: “searches/day,” “snapshot views/day,” “browse views/day.”
  - If you don’t measure, say so explicitly and rely on partner/verifier evidence.

### Status page layout example (content, not UI code)

Top:
- “Current status: Operational / degraded / outage” (based on `/api/health` + static incident notes)
- “Last updated: <timestamp>”

Coverage:
- Sources tracked: N
- Snapshots: N
- Pages: N

Per-source table:
- Source name
- Records captured
- First capture date
- Last capture date
- “Browse archived site” link (if entry point exists)

Reliability:
- “Uptime last 30 days” (if you have an external monitor)
- “Recent incidents” (manual log is fine initially)

### Acceptance criteria

- The status/metrics page works even when replay is not configured (and does not leak admin endpoints).
- The status/metrics page is explicit about what is measured and what is not.

### Phase 2 Implementation Plan (Detailed; sub-phases)

Phase 2 is about *credibility through measurability*. The goal is not “more numbers”; it’s to make HealthArchive look and behave like a maintained public service with transparent coverage and clear limitations.

#### Design principles (Phase 2)

- **Honesty over completeness.** Only publish metrics you can define and reproduce.
- **Privacy-first.** Prefer aggregated counts over user-level tracking. Default to collecting *less*.
- **No new risk surface by accident.** Avoid adding third-party scripts until you have explicitly decided to do so and updated CSP accordingly.
- **Keep public vs admin boundaries strict.** The status page must not depend on admin-only endpoints.
- **Single-VPS realism.** Any new aggregation pipeline must be lightweight and must not compete with indexing/crawling for CPU/IO.

#### Sub-phase 2A — Decide the “official” metric contract (½–1 day)

**Goal:** Create a stable set of metrics with explicit definitions, and decide how you’ll measure usage (or explicitly not).

Deliverables:

- A “Metrics definitions” section (can live on the new `/status` page, or as a short doc linked from it).
- A decision on **usage measurement** (see Sub-phase 2D).

Recommended official metrics (initial set):

1) **Coverage**
   - `sourcesTotal`, `pagesTotal`, `snapshotsTotal` (already available via public API).
   - Per-source:
     - `recordCount`, `firstCapture`, `lastCapture` (already available via `/api/sources`).

2) **Freshness**
   - Per-source “last capture date” (proxy for freshness).
   - Optional later: “time to index” (requires tracking job completion vs indexing completion).

3) **Reliability**
   - Public: “API reachable” (based on `/api/health`).
   - Public: “Replay enabled” and “previews enabled” (based on whether URLs are returned).
   - External monitor uptime (%): start as “not yet measured” or “measured externally” until you have a real monitor.

4) **Usage (optional, and only if you choose)**
   - `searchRequestsPerDay`, `snapshotViewsPerDay`, `browseViewsPerDay`, `reportSubmissionsPerDay` (aggregated).
   - If you do *not* measure usage, state that explicitly in `/privacy` and in the metrics definitions.

Acceptance criteria:

- Each metric has: definition, data source, and update cadence (e.g., “computed nightly” vs “live”).

#### Sub-phase 2B — Public Status/Metrics page MVP (frontend + existing endpoints) (1–2 days)

**Goal:** Ship a status page that works immediately without new backend changes.

Recommended route:

- `/status` (or `/status` + `/metrics` as a separate section; avoid naming collisions with backend `/metrics`).

Data sources (public endpoints only):

- `/api/health` → “API status”
- `/api/stats` → totals
- `/api/sources` → per-source coverage table

Recommended sections (copywriting guidance):

- **Current status**
  - “Operational / degraded / down” (simple, based on whether `/api/health` succeeds).
  - Timestamp: “Last checked: …”
  - Small note: “This is a public archive; not medical advice.”

- **Coverage snapshot**
  - Sources tracked, snapshots, pages
  - Latest capture date (if available via stats)

- **Per-source coverage**
  - Source name + counts + first/last capture
  - Link to browse entry point (if available)

- **Data notes**
  - What “missing” can mean (not captured yet, capture failure, out of scope)
  - Replay limitations and third-party asset caveats

Acceptance criteria:

- Page loads even if the backend is down (show an honest fallback message; do not fabricate metrics).
- Page stays within existing security posture (no new scripts; no CSP weakening).

#### Sub-phase 2C — “Metrics definitions” and transparency language (½ day)

**Goal:** Prevent “numbers with vibes” by publishing the meaning and limits of each metric.

Include:

- What counts as a snapshot vs a page group
- Timezones (capture dates are UTC)
- What “last capture date” means (it is not necessarily “last updated by the source”)
- Whether usage metrics are collected (and if so, how coarse they are)

Acceptance criteria:

- A researcher can quote your definitions in a methods section without guesswork.

#### Sub-phase 2D — Decide and implement a usage measurement approach (plan-level options) (1–4 days)

This is the key decision point. Choose one:

**Option D1: No usage analytics (privacy-first)**
- Publish only coverage + freshness + reliability proxies.
- Evidence of impact comes from: partner adoption, citations, verifier statements, issue report volume.
- Lowest risk and operational overhead.

**Option D2: Server-side aggregate counters (recommended “best practice” for your posture)**
- Track *only aggregated daily counts* for key public actions:
  - search requests, snapshot detail views, raw snapshot views, browse page loads, report submissions
- Store aggregates (e.g., per day) in the backend DB, not user-level logs.
- No cookies and no third-party scripts required.
- This is usually the best balance: measurable impact without surveillance optics.

**Option D3: Privacy-preserving third-party analytics**
- Only if you explicitly decide to accept the CSP and supply-chain tradeoffs.
- Must update `/privacy` to reflect the provider and data collected.

Acceptance criteria:

- Whatever option you pick is reflected in `/privacy` and on the status page.

#### Sub-phase 2E — Reliability measurement + incident notes (½–2 days, incremental)

**Goal:** Make “service operations” legible without pretending you have enterprise SRE.

Approach:

- Start with a manual “Recent incidents” section on `/status` (or `/changelog` entries tagged “incident”).
- Add external uptime monitoring later (recommended by existing ops docs) and then display 30-day uptime once you have a data source you trust.

Acceptance criteria:

- If something breaks, you have a public place to acknowledge it (even briefly).

#### Sub-phase 2F — Monthly Impact Report (template + storage + cadence) (½–1 day)

**Goal:** Create a repeatable “proof artifact” that builds ABS verifiability over time.

Deliverables:

- A template (web page or markdown) with these sections:
  - “What’s new”
  - “Coverage changes”
  - “Reliability notes”
  - “Usage snapshot” (only if you measure)
  - “Partner/mention highlights”
  - “Known limitations / next month focus”

Storage/location:

- Keep impact reports in a stable folder (e.g., `docs/impact/YYYY-MM.md` in the frontend repo, or a dedicated section on the site).
- Link each report from `/changelog`.

Acceptance criteria:

- You can produce the report in under 30 minutes each month.

#### Sub-phase 2G — Documentation + runbook updates (½ day)

- Update:
  - Frontend docs to mention `/status` and where its data comes from.
  - Backend docs if new public endpoints or aggregation logic are introduced.
- Add a changelog entry: “Status/Metrics page launched; metrics definitions published.”

#### Sub-phase 2H — Tests (as behavior changes) (½–2 days)

- Frontend: add tests that `/status` renders expected sections (and handles “backend unreachable” gracefully).
- Backend (only if Option D2 is implemented): add tests that aggregates update correctly and do not store PII.

**Definition of done (Phase 2, detailed)**

- A public `/status` page exists and answers: “Is it up? what’s covered? how current? what are the limitations?”
- Metric definitions are published and consistent with `/privacy`.
- If usage is measured, it is aggregated and privacy-preserving; if not, that is explicitly stated.
- A monthly impact report template exists and is linked from `/changelog`.

**Status (Phase 2 implementation)**

- Implemented on 2025-12-21.
- Added `/status` and `/impact` pages to the frontend.
- Added `/api/usage` to the backend with daily aggregate counts (search, snapshot detail, raw snapshot, reports).
- Updated `/privacy` to disclose aggregate usage counts.
- Added a baseline impact report and changelog entry for Phase 2.

--- 

## Phase 3 — Change Tracking + Compare + Digest (expanded)

### Why this matters

Without change tracking, HealthArchive answers “what did it say?” but not “what changed?”.
Change tracking is the primary upgrade that drives:

- repeat visits,
- subscriber growth,
- and research/journalism use (audit trails).

### What already exists that should be leveraged

- **Canonical page grouping:** `normalized_url_group` and the optional `pages` table (`healtharchive-backend/src/ha_backend/models.py`).
- **Content hashing:** snapshots already store `content_hash` (SHA-256 of body bytes) (`healtharchive-backend/src/ha_backend/indexing/mapping.py`).
  - This is a powerful “cheap change detector” before doing expensive diffs.
- **Search modes:** `view=pages` vs `view=snapshots` and strong filtering semantics (`healtharchive-backend/docs/architecture.md`).
- **UI foundations:** snapshot and browse views already handle provenance, replay/raw fallback, and edition switching.

### What “change tracking” must not be

- It must not read like medical interpretation.
- It must not summarize in a way that implies “this means you should…”.
- It must not depend on heavy compute during normal user requests.

### Deliverables (conceptual)

1) **Page timeline**
   - For a given “page group,” show the list of captures over time (dates, editions/jobs).
   - User can select two captures to compare.

2) **Compare view**
   - Show two versions (A and B) with clear provenance: timestamps, source, URL, edition/job.
   - Provide a descriptive diff:
     - “Added/removed/changed” section counts
     - Highlight textual changes
     - Explicitly label “high-noise” pages when appropriate

3) **Changes feed**
   - A feed of recent change events, filterable by:
     - source,
     - date range,
     - (optional later) mechanical tags (avoid editorial topics early)

4) **Digest**
   - Weekly digest published as:
     - web page archive,
     - RSS feed,
     - email later (if desired).

### Practical “meaningful change” policy (examples)

Meaningful changes might include:
- Headings/sections added/removed
- Guidance text changed in paragraphs or lists
- Tables updated (when text extraction can detect it)

Not meaningful (or “low signal”) changes include:
- cookie banners,
- global nav,
- timestamps “last updated” in page chrome,
- minor layout/whitespace shifts.

### Suggested approach (still no code)

Use a staged pipeline so you don’t compute diffs unnecessarily:

1) Detect change candidates:
   - If `content_hash` differs between successive captures for the same page group, it’s a candidate.
2) Generate a “readable text representation”:
   - Normalize HTML to text with noise reduction rules.
3) Produce diff artifacts:
   - Store enough to render compare views and feeds quickly.
4) Surface the output in UI:
   - Compare page, timeline page, changes feed, digest feed.

### User-facing copy examples (guardrail language)

Compare page disclaimer:

> This comparison highlights **text changes** between two archived captures. It does not interpret the change or provide guidance. For current recommendations, consult the official source website.

Digest disclaimer:

> This digest lists pages whose **archived text changed** during the period. It is not clinical guidance and may include formatting or boilerplate changes.

### Acceptance criteria

- A user can answer “what changed since the last edition (or between editions)?” without manual searching.
- Compare output is descriptive, provenance-rich, and clearly non-authoritative.
- Heavy processing happens off the request path (no slow compare pages that compute diffs live).

### Phase 3 Implementation Plan (Detailed; sub-phases)

Phase 3 turns HealthArchive from “a searchable archive” into “a living audit tool”.
The goal is to let a user answer: **what changed, when, and between which captures** — without
implying medical interpretation.

#### Design principles (Phase 3)

- **Descriptive only:** show *text diffs*, not “meaning” or “recommendations”.
- **Provenance-first:** every change event must be anchored to two snapshot IDs (A → B) with timestamps and the source URL/group.
- **No heavy work on requests:** diff computation must happen in background/ops workflows, not inside normal page loads.
- **Noise-aware:** explicitly label high-noise pages and avoid overconfident summaries.
- **Versioned methodology:** store a `diff_version`/`normalization_version` so later improvements don’t silently rewrite history.
- **Scope fits the annual edition model:** a user should still get value even when captures are annual (timeline + compare remain useful even if “weekly changes” are sparse).

#### Sub-phase 3A — Define “change events” and user stories (½–1 day)

Define the minimum set of user-facing questions and map each to an artifact:

- **Timeline:** “Show me all captures for this page over time.” → *page timeline dataset*
- **Compare:** “Show changes between capture A and B.” → *diff artifact for (A,B)*
- **Recent changes:** “What changed recently?” → *changes feed over computed events*
- **Digest:** “Summarize changes for a period.” → *web + RSS output derived from the feed*

Also define change types and guardrails:

- `updated` (content changed between two captures)
- `unchanged` (hash identical; no diff needed)
- `new_page` (first-ever capture for a page group)
- `removed_page` (optional; requires edition-to-edition set comparison)
- `error` (diff could not be computed; still track the event)

Deliverable: a short “Phase 3 semantics” section (internal) that is used consistently in API/UI copy.

#### Sub-phase 3B — Data model + migration plan (1–2 days)

Create a minimal, future-proof storage model for change tracking:

- **Snapshot pair anchor:** store `from_snapshot_id` and `to_snapshot_id` (or `a_snapshot_id`/`b_snapshot_id`).
- **Page anchor:** store `source_id` + `normalized_url_group` (and/or `page_id` if you choose to make pages mandatory).
- **Summary fields (fast feed rendering):**
  - timestamps, diff size signals (e.g., “changed characters”, “changed sections count”),
  - coarse “noise score” / “high-noise” boolean,
  - short, descriptive “what changed” sentence that never implies interpretation (e.g., “3 sections changed; 2 added; 1 removed”).
- **Diff artifact fields (for compare rendering):**
  - a rendered HTML diff or structured diff blocks,
  - optional “section list” for navigation.
- **Version fields:** `diff_version`, `normalization_version`, `computed_at`, `computed_by`.

Deliverable: one Alembic migration introducing change-event storage (plus indexes needed for feeds and timelines).

#### Sub-phase 3C — Change detection pipeline (background compute) (2–5 days)

Build a staged pipeline that minimizes work:

1) **Identify candidates cheaply**
   - Use existing `normalized_url_group` + `capture_timestamp` ordering to find “adjacent captures”.
   - Use `content_hash` to classify `unchanged` vs `candidate` (skip expensive diff when hashes match).
2) **Normalize HTML to “diffable text”**
   - Reuse the project’s text extraction approach where possible.
   - Add noise-reduction rules (e.g., drop headers/footers/nav/cookie banners/“last updated” chrome where detectable).
3) **Compute diff**
   - Produce a human-readable diff with stable formatting.
   - Emit “summary stats” (counts only, no interpretations).
4) **Persist artifacts**
   - Store event row + (optional) rendered diff artifact for fast compare pages.

Operational requirement: the pipeline must be **idempotent**, resumable, and rate-limited so it doesn’t overwhelm the VPS after big crawls.

Deliverables:

- A command or background task that computes diffs for:
  - “newly indexed snapshots”, and
  - a backfill range (for existing data).
- A consistent way to measure backlog and error rate (even if only as counts).

#### Sub-phase 3D — Public API contract (1–3 days)

Expose a public-only contract that supports UI and research workflows:

- **Changes feed endpoint**
  - filterable by source, date range, and (optionally) URL/group.
  - supports pagination.
  - returns summary + provenance fields (snapshot IDs A/B, timestamps, source, URL/group).
- **Compare endpoint**
  - fetch a diff artifact for two snapshots (or a precomputed diff ID).
  - returns:
    - provenance,
    - summary stats,
    - diff content (renderable).
- **Page timeline endpoint**
  - list captures for a given URL group (or snapshot ID → group resolution).
  - enables UI selection of A and B.

Deliverables: updated schema docs (Pydantic) and a short API section in the backend README (public endpoints only).

#### Sub-phase 3E — Frontend UX: timeline, compare, changes (2–6 days)

Implement three user-facing surfaces:

1) **Changes page** (`/changes`)
   - “Changes” feed with filters (source + date range).
   - Default view should be **edition-aware** (e.g., “changes in the latest edition” or “between edition A and B”), because the project’s default capture cadence is annual.
   - A “last N days” view can exist, but must be labeled as **recently archived** (capture time), not “recently updated by the source.”
   - Each entry shows:
     - what changed (descriptive summary),
     - capture timestamps (UTC labeling),
     - links to compare and to each snapshot.
2) **Compare view** (`/compare` or equivalent)
   - Clear A/B selection and provenance.
   - Diff display with:
     - obvious “archived content” banner,
     - navigation by changed sections (if available),
     - warnings for high-noise pages.
3) **Snapshot timeline integration**
   - On snapshot pages, add “Other captures of this page” (timeline list).
   - Allow selecting a second snapshot to compare.

Guardrail copy must be present on compare and changes pages (descriptive-only; link to official sources for current guidance).

#### Sub-phase 3F — Digest MVP: web + RSS (1–3 days)

Start with low-ops digest channels:

- **Digest index page** (`/digest`)
  - Explains what the digest is (a list of changed pages) and what it is not (guidance).
  - Links to RSS feeds.
- **RSS feeds**
  - “Global changes” RSS.
  - Optional per-source RSS.

Deliverable: a digest archive concept that doesn’t require email infrastructure yet.

#### Sub-phase 3G — Documentation and governance alignment (½–1 day)

Update public-facing docs to match the new capability:

- Methods/governance text explaining:
  - what “change tracking” means,
  - limitations (noise, missing captures, replay limitations),
  - “descriptive only” stance.
- Researcher guidance:
  - how to cite a compare view (A and B snapshot IDs + timestamps).

#### Sub-phase 3H — Tests + performance gates (1–3 days)

Add tests that protect the core promise:

- Backend:
  - candidate detection logic (hash match skips diff),
  - changes feed pagination and filtering,
  - compare endpoint returns stable provenance,
  - disabled modes (feature flag off) behave predictably.
- Frontend:
  - `/changes` renders with mocked API data,
  - compare view renders provenance and disclaimer,
  - graceful behavior when API is unavailable (fallback messaging).

Performance gates:

- No compare request should trigger heavy diff computation synchronously.
- Feed endpoints should be index-backed and fast for large datasets.

**Status (Phase 3 implementation)**

- Implemented on 2025-12-22.
- Added a `snapshot_changes` table and precomputed diff artifacts.
- Introduced `ha-backend compute-changes` for backfill + incremental diffing.
- Added public APIs: `/api/changes`, `/api/changes/compare`, `/api/changes/rss`, `/api/snapshots/{id}/timeline`.
- Added frontend pages `/changes`, `/compare`, `/digest` plus snapshot timeline UX.
- Updated governance/methods/researcher copy and changelog to reflect change tracking.
- Added systemd timer templates for scheduled change tracking runs (see `docs/deployment/systemd/README.md`).

--- 

## Phase 4 — Distribution + External Validation (expanded)

### Why this matters

External validation is the “credibility multiplier”:

- It makes the project verifiable to outsiders.
- It reduces “this is just a personal project” framing.
- It provides sustainable feedback loops (what’s useful vs noise).

### Deliverables

1) **Partner target list (10 targets is enough)**
   - Libraries/archives (digital scholarship)
   - Journalism programs/labs
   - Public health research groups
   - Educator networks (critical appraisal / evidence communication)

2) **Partner pitch assets**
   - One-page brief (mission + disclaimers + what they get)
   - Screenshot pack (search, snapshot, compare, digest)
   - “How to cite a snapshot” guidance

3) **Distribution mechanism**
   - Lowest friction: RSS feeds (digest, per-source changes)
   - Next: embed widget for “recent changes”

4) **Verifier strategy**
   - One credible person willing to attest to:
     - your role,
     - the project’s utility,
     - and (ideally) how they used it.

### Acceptance criteria

- At least one external partner links or embeds.
- At least one named verifier agrees (with permission) to validate your role and impact.

---

### Phase 4 Implementation Plan (Detailed; sub-phases)

Phase 4 is intentionally **mostly non-code**. The outputs that “count” are public artifacts and third-party confirmations (links/embeds, emails/letters, citations/mentions) rather than features.

#### Design principles (Phase 4)

- **Distribution before complexity.** Prefer RSS + “link to `/changes`” over building bespoke widgets.
- **No medical interpretation.** Partners should be distributing an archive/change log, not “recommendations”.
- **Permission + accuracy.** Never list a partner or verifier publicly without explicit permission and a reviewed description.
- **Privacy by default.** Do not collect or store partner contact details in the repo. Keep outreach tracking private.
- **Evidence-first.** Every claimed partnership should have a “proof artifact” you can show (screenshot, published link, email permission).

#### Sub-phase 4A — Define target partners + selection criteria (½–1 day)

**Goal:** A small, realistic list of targets you can actually contact and close.

Decide:

- The **primary distribution channel** for Phase 4 (recommended): the `/digest` RSS feeds (global + per-source).
- The **default “ask”** (keep it easy): “Please link to HealthArchive’s digest/changes page as a resource for reproducibility and auditability.”
- Partner “tiers” (avoid overpromising):
  - **Distribution partner:** links to `/digest` or `/changes`, or republishes the RSS feed in a resource page.
  - **Review partner:** provides occasional feedback on governance/scope wording (informal advisory).
  - **Research/teaching partner:** uses it in a class/lab project and is willing to be named (optional; high value).

Selection criteria for the initial list (use as a filter):

- Audience overlap with HealthArchive (research methods, journalism, digital scholarship, evidence communication).
- Ability to “say yes” quickly (a librarian maintaining a LibGuides page, a lab website admin, a newsletter editor).
- Comfort with the “archive, not guidance” framing.
- Willingness to be publicly named and/or verify use.

Deliverables:

- A private “Top 10 targets” list with:
  - org name, relevant program/page, role/title to contact,
  - one sentence: why they are a fit,
  - one sentence: what you’re asking them to do,
  - the lowest-friction next step (email vs warm intro vs office hours).

Proof artifacts (later):

- Screenshot or archived copy of their page once they link/mention it.

#### Sub-phase 4B — Build partner-ready assets (1–2 days)

**Goal:** Remove friction for adoption and prevent misinterpretation.

Create (minimum viable kit):

1) **One-page brief** (PDF or web page)
   - “What it is” in 2 sentences (archive + timestamped change tracking).
   - “What it is not” (not medical advice; not current guidance; not affiliated).
   - Who it is for (researchers/journalists/educators first).
   - Links: `/methods`, `/governance`, `/status`, `/impact`, `/digest`, `/changes`.
   - A small “project snapshot” box: sources tracked, snapshots, latest capture date.

2) **Screenshot pack** (5–8 images)
   - Home + “What this is/isn’t”
   - Archive search page
   - Snapshot page metadata + report link
   - Changes feed (`/changes`)
   - Compare view (`/compare?to=<real id>`) showing “descriptive only”
   - Digest page (`/digest`) with RSS links
   - Status/Impact pages (optional but persuasive)

3) **“How to cite” handout** (1 page)
   - Snapshot citation format (already present on `/researchers`; refine into a stable standalone artifact).
   - Compare citation format (two snapshot IDs + timestamps + compare URL).
   - A clear disclaimer that citations refer to archived content, not current guidance.

4) **Outreach email templates** (plain text)
   - One initial email template per partner tier (distribution vs research/teaching).
   - A 2–3 sentence “elevator pitch” that avoids adversarial language (“auditability” rather than “accountability attack”).

Rules:

- Do not include private emails/phone numbers in repo artifacts.
- Do not include claims like “widely used” until you can support them with numbers/partners.

Deliverables location guidance:

- Public-facing, non-sensitive assets can live in the repo (docs folder) if you want versioning.
- Contact lists and outreach logs should stay private (not committed).

#### Sub-phase 4C — Outreach workflow + tracking (1–2 weeks, ongoing)

**Goal:** Run outreach like a small operational process, not ad-hoc messages.

Suggested workflow:

1) Identify 3 “warmest” targets (fastest to yes).
2) Send the initial email with:
   - one-page brief link/attachment,
   - link to `/digest` and `/changes`,
   - one specific ask,
   - one low-effort follow-up option (“If helpful, I can send a 2-minute screencast or hop on a 15-minute call.”).
3) Track each outreach attempt privately:
   - date sent, response, follow-up dates, outcome.
4) Follow-up cadence:
   - 7 days after initial email,
   - 14 days after initial email (final polite close).

Example “distribution ask” (keep it concrete):

- “Would you be willing to add HealthArchive.ca to your digital scholarship/public health methods resources page, linking to the digest (`/digest`) or the changes feed (`/changes`)? The site is explicitly non-authoritative and is intended for reproducibility and auditability.”

Proof artifacts (save for ABS/verifiers):

- Email thread granting permission to be listed (even a short “Yes, you can list us” reply).
- Screenshot of the published link on their site (with date).

#### Sub-phase 4D — Partner onboarding (½ day per partner)

**Goal:** Make “yes” immediately turn into public evidence, with minimal partner burden.

Distribution options (in order):

1) Link to `/digest` (RSS + explainer).
2) Link to `/changes` (edition-aware changes feed).
3) Optional: partner adds the RSS feed to their own page/newsletter tooling.

What you provide back:

- A short “How to use this resource” blurb they can paste (includes disclaimers).
- The exact RSS URL(s) for global/per-source feeds.
- A suggested citation sentence: “This is an archival record; verify current guidance on the official source.”

What you avoid:

- Anything that looks like endorsement or clinical guidance.
- Anything requiring ongoing support (custom widgets, per-partner deployments) until you have bandwidth.

#### Sub-phase 4E — Verifier strategy + verification packet (½–1 day)

**Goal:** Make it easy for one credible person to verify your role and the project’s use/impact.

Identify one verifier candidate (examples):

- librarian/archivist in digital scholarship,
- public health researcher supervising a methods project,
- journalism faculty/editor using the resource in a workflow.

Build a small “verification packet” (shareable link or PDF):

- Mission + safety posture (one paragraph).
- What you built (high-level architecture, no sensitive infrastructure details).
- What is live now (key pages + APIs).
- Metrics snapshot (from `/status` + `/impact`).
- What you do operationally (annual capture policy + change tracking cadence).
- A short role summary (what you personally did; keep it factual).

Ask the verifier explicitly:

- “Are you willing to verify that I built and operate this project and that it has been useful for X purpose?”
- “May I list your name/title as a verifier (with your preferred wording)?”

Proof artifact:

- Written confirmation (email is sufficient) saved privately.

#### Sub-phase 4F — Mentions/citations capture (ongoing; 15 min/month)

**Goal:** Build an evidence trail without invasive analytics.

Keep a lightweight, public “mentions log” (can be a section in the changelog or impact report):

- date, outlet/org, link, one-sentence context.

Ways to find mentions (non-invasive):

- manual search for “healtharchive.ca” occasionally,
- direct partner updates (“we added you to our resources page”),
- optional: set up alerts outside the repo (do not commit credentials).

#### Sub-phase 4G — Phase 4 definitions of done (make success measurable)

Minimum viable “Phase 4 complete”:

- 1 distribution partner publicly links to `/digest` or `/changes` (with permission to name them).
- 1 verifier agrees (with permission) to verify your role and describe the project’s utility.
- 1 impact report includes the above as “Partner highlights” with links.

Stretch goals (high value if feasible):

- 1 course/lab/student project uses the data and is willing to be named.
- 1 external mention in a newsletter/blog post/paper.

**Status (Phase 4 assets implemented)**

- Public one-page brief page: `https://www.healtharchive.ca/brief` (downloadable Markdown at `/partner-kit/healtharchive-brief.md`)
- Public citation guidance page: `https://www.healtharchive.ca/cite` (downloadable Markdown at `/partner-kit/healtharchive-citation.md`)
- Partner kit draft: `healtharchive-backend/docs/operations/phase-4-partner-kit.md`
- One-page brief (source copy): `healtharchive-backend/docs/operations/phase-4-one-page-brief.md`
- Citation handout (source copy): `healtharchive-backend/docs/operations/phase-4-citation-handout.md`
- Outreach templates: `healtharchive-backend/docs/operations/phase-4-outreach-templates.md`
- Verification packet outline: `healtharchive-backend/docs/operations/phase-4-verification-packet.md`
- Mentions log template: `healtharchive-backend/docs/operations/phase-4-mentions-log-template.md`

Remaining Phase 4 work is outreach and external validation.
## Phase 5 — Research-Grade Outputs (expanded)

### Why this matters

HealthArchive’s strongest natural audience is research/journalism. Making it “research-grade” increases:

- citations/mentions,
- reuse in student projects,
- and the credibility of the archive as an artifact.

### What already exists

- A public citation page exists (`/cite`) and is linked from `/researchers`.
- Change tracking exists (edition-aware `/changes`, `/compare`, `/digest` with RSS).
- Backend exposes stable, structured APIs for search, sources, snapshots, timeline, and changes.

### Design principles (Phase 5)

- **Research-first, not “consumer guidance.”** Optimize for reproducibility, auditability, and stable references.
- **No medical interpretation.** Outputs are descriptive and methodological; avoid “what this means medically.”
- **No personal data.** Exports must not include IP addresses, emails, user agents, or raw report submissions.
- **Stable identifiers + clear versioning.** Prefer durable URLs and explicit “edition” framing over “latest” unless carefully labeled.
- **Sustainable access.** Default to lightweight endpoints and cached/exported files; heavy requests go through a human workflow first.
- **Public methods + limitations.** Avoid overclaiming completeness or authoritative status.

### Phase 5 Implementation Plan (Detailed; sub-phases)

Phase 5 has two parallel goals:

1) make HealthArchive easier to cite, reuse, and defend in research/journalism, and  
2) create “proof artifacts” that demonstrate sustained, measurable public-interest value.

#### Sub-phase 5A — Citation guidance (stabilize + make it unambiguous)

**Goal:** A researcher should be able to cite a snapshot or comparison correctly in under 60 seconds.

Deliverables:

- Keep `/cite` as the canonical citation page, and ensure it covers:
  - snapshot citations,
  - compare-view citations (two snapshot IDs + both capture timestamps),
  - a short “fields glossary” (title, original URL, capture timestamp, snapshot URL).
- Add a “How to cite” link from relevant surfaces:
  - `/snapshot/[id]` (near metadata)
  - `/compare` (near the comparison header)
  - `/changes` and `/digest` (as “for researchers” guidance)
- Add a lightweight citation “ready-to-copy” format (descriptive only):
  - snapshot citation template populated from snapshot metadata
  - compare citation template populated from both snapshot IDs/timestamps

Non-goals / guardrails:

- Do not suggest citation formats that imply endorsement or “official guidance.”
- Do not add any user tracking to measure citations; citations are measured by mentions/logs.

Acceptance criteria:

- Citation formats match the site’s actual stable URLs and timestamps (UTC).
- A neutral user can follow the instructions without needing to infer missing fields.

#### Sub-phase 5B — Research access pathway (public “how to request” + expectations)

**Goal:** Make bulk/research access possible without promising infinite bandwidth or completeness.

Deliverables:

- A public “Research access” section (could live on `/researchers` or a new `/research` page) that states:
  - what data is available today (UI + public API endpoints + exports when available),
  - what isn’t available yet (and why),
  - how to request bulk access or a targeted export (email or a structured request form),
  - sustainability constraints (rate limits, caching, reasonable use).
- A standardized request checklist (for the requester to provide):
  - source(s) and date range(s),
  - whether they want per-snapshot vs per-page grouping,
  - whether they want “edition-to-edition” changes or within-edition diffs,
  - intended use (paper, class project, journalism piece).

Examples (plain-language):

- “I need all Health Canada snapshots between Apr 1–May 1, 2025, plus change events between editions.”
- “I need diffs for these 25 URLs and the two most recent captures of each.”

Acceptance criteria:

- Someone unfamiliar with the codebase can understand how to request research access and what they’ll receive.

#### Sub-phase 5C — Machine-readable exports (metadata first, then diffs)

**Goal:** Provide research-friendly data without turning the public API into an unbounded bulk download service.

**Recommended defaults (decisions)**

- **Canonical format:** `JSON Lines` (`.jsonl`) compressed with `gzip` (`.jsonl.gz`)
  - Why: streamable, append-friendly, handles optional fields cleanly, works well for large datasets, and is robust to schema evolution.
- **Convenience format:** `CSV` compressed with `gzip` (`.csv.gz`)
  - Why: easy for non-programmers (Excel/R), still compact when gzipped.
- **Release packaging:** always include a small `manifest.json` alongside exports
  - Why: makes releases citable and reproducible (schema version, date ranges, source list, row counts, checksums).
- **No raw content by default:** exclude full `diff_html` and raw snapshot HTML from exports
  - Why: keeps exports lightweight and reduces misuse risk; raw content stays accessible via the UI and specific snapshot URLs.

Export tiers (recommended):

- Tier 0 (public, lightweight, always on):
  - Snapshot metadata export (no raw HTML content).
  - Change event export (no full diff bodies unless already computed and safe to expose).
  - Clear schema/field definitions (“data dictionary”).
- Tier 1 (public, cached files):
  - Pre-generated exports per edition or per month, hosted as static files.
- Tier 2 (by request):
  - Larger custom exports prepared offline for a study/class/journalism project.

Deliverables:

- Define a stable export schema (same field names across `.jsonl` and `.csv`):
  - **Snapshots export (minimum viable)**
    - `snapshot_id`
    - `source_code`, `source_name`
    - `captured_url` (as archived)
    - `normalized_url_group` (canonical grouping key)
    - `capture_timestamp_utc` (ISO-8601, UTC)
    - `language`, `status_code`, `mime_type`
    - `title` (optional but recommended)
    - `job_id`, `job_name` (edition anchor)
    - `snapshot_url` (absolute; e.g., `https://www.healtharchive.ca/snapshot/123`)
  - **Changes export (minimum viable)**
    - `change_id`
    - `source_code`, `source_name`
    - `normalized_url_group`
    - `from_snapshot_id`, `to_snapshot_id`
    - `from_capture_timestamp_utc`, `to_capture_timestamp_utc`
    - `from_job_id`, `to_job_id`
    - `change_type`, `summary`
    - `added_sections`, `removed_sections`, `changed_sections`
    - `added_lines`, `removed_lines`, `change_ratio`
    - `high_noise`, `diff_truncated`
    - `diff_version`, `normalization_version`, `computed_at_utc`
    - `compare_url` (absolute; e.g., `https://www.healtharchive.ca/compare?from=…&to=…`)
- Add a “data dictionary” page in docs and link to it from the export endpoint and `/researchers`.
- Add clear limitations:
  - coverage depends on scope rules and capture success,
  - replay fidelity varies,
  - change tracking reflects what was captured, not what changed on the source site in real time.

Acceptance criteria:

- Exports are stable, documented, and do not expose personal data or admin-only fields.
- Exports are edition-aware by default, to avoid misleading “recent changes” claims.

#### Sub-phase 5D — Dataset releases (versioned, citable artifacts)

**Goal:** Create periodic “research objects” that can be cited and referenced over time.

**Recommended defaults (decisions)**

- **Where to publish:** GitHub Releases (initially)
  - Why: simple, already in your workflow, supports attaching files + checksums, and provides stable URLs for citation.
- **Release cadence:** monthly if the archive is actively changing; quarterly if updates are mostly annual
  - Why: cadence should reflect real capture rhythm to avoid “activity theatre.”
- **Release contents:** `snapshots.jsonl.gz`, `snapshots.csv.gz`, `changes.jsonl.gz`, `changes.csv.gz`, `manifest.json`, `SHA256SUMS`
  - Why: two common formats + reproducible integrity metadata.

Deliverables:

- A simple release cadence (monthly or quarterly) for:
  - snapshot metadata dumps,
  - change event dumps,
  - a short release note/changelog entry summarizing what changed.
- Versioning approach (example):
  - `healtharchive-dataset-YYYY-MM` with checksums.
- A release checklist:
  - schema version,
  - date range included,
  - source list included,
  - checksums generated,
  - known limitations noted.

Where to publish:

- Prefer GitHub Releases (with checksums) or a dedicated static dataset page, depending on file sizes and operational comfort.

Acceptance criteria:

- A researcher can cite “Dataset release YYYY-MM” and reproduce the exact file they used.

#### Sub-phase 5E — Methods note / poster (scholarship output)

**Goal:** Produce one scholarly artifact that is methodological and defensible.

Recommended topic framing (non-interpretive):

- “A provenance-first pipeline for archiving Canadian public health webpages.”
- “Edition-aware change tracking for public health web guidance: methods and limitations.”

Deliverables:

- A methods note outline with:
  - motivation (reproducibility + auditability),
  - capture methodology (WARCs + indexing),
  - provenance labeling policy,
  - change tracking approach (normalization + diff),
  - limitations and non-goals (not guidance, not medical advice),
  - ethics/privacy posture (no PHI; aggregated usage metrics only),
  - a small descriptive results section (counts and examples, not interpretation).
- A “figure plan”:
  - architecture diagram (high-level),
  - example change timeline for a single URL,
  - coverage table (sources + capture windows).

Acceptance criteria:

- The artifact can be shared publicly without creating medical guidance liability.

#### Sub-phase 5F — Measurability (research adoption signals)

**Goal:** Collect defensible evidence that research-grade outputs are used.

Deliverables:

- A mentions/citations log process (already templated in Phase 4) updated monthly.
- A “research use” section in monthly impact reports:
  - number of research inquiries,
  - number of bulk exports delivered (if any),
  - any public citations/mentions (with links).

Acceptance criteria:

- You can point to concrete, verifiable research adoption signals without tracking individuals.

### Definition of done (Phase 5)

Minimum viable “Phase 5 complete”:

- `/cite` is canonical and linked from snapshot/compare/changes surfaces.
- A public research access pathway exists (how to request bulk access + constraints).
- A v1 export exists (snapshot metadata + change events) with a documented schema.
- One “methods note” outline exists and is ready to submit as a poster/preprint/blog-style methods write-up.

Stretch goals:

- Versioned dataset releases on a fixed cadence (monthly/quarterly) with checksums.
- At least one external research/journalism project uses an export and can be cited in the mentions log.

**Status (Phase 5 assets implemented)**

- Public export manifest and endpoints: `/api/exports`, `/api/exports/snapshots`, `/api/exports/changes`.
- Public data dictionary page: `https://www.healtharchive.ca/exports` (+ downloadable Markdown).
- `/researchers` updated with research access workflow and export manifest link.
- `/cite` linked from `/snapshot`, `/compare`, `/changes`, and `/digest`.
- Export schema documented in `healtharchive-backend/docs/operations/exports-data-dictionary.md`.
- Export env toggles documented (`HEALTHARCHIVE_EXPORTS_ENABLED`, defaults, and limits).

Remaining Phase 5 work: dataset release cadence + external research adoption.

---

## Phase 6 — Reliability + Sustainability (expanded)

### Why this matters

You already have strong ops docs and an annual campaign definition. The upgrade here is making:

- cadence,
- constraints,
- and operational discipline

explicit and sustainable.

### What already exists

- Annual campaign scope and seeds (`healtharchive-backend/docs/operations/annual-campaign.md`)
- Single VPS production runbook (`healtharchive-backend/docs/deployment/production-single-vps.md`)
- Monitoring and CI checklist (`healtharchive-backend/docs/operations/monitoring-and-ci-checklist.md`)
- Optional systemd timers for annual scheduling and verification (`healtharchive-backend/docs/deployment/systemd/README.md`)

### Deliverables

1) **Public capture cadence policy**
   - Annual edition: Jan 01 UTC
   - Exceptions: what qualifies for ad-hoc captures
   - Why scope is limited (reliability > breadth)

2) **Ops cadence (internal)**
   - weekly: health review
   - monthly: reliability review + impact report
   - quarterly: restore test
   - routine dependency patching

3) **Growth constraints**
   - Storage budget
   - Source cap per year
   - Performance budgets
   - Explicit posture on replay retention (WARCs must remain available for replay)

### Acceptance criteria

- There is a clearly documented “how we operate” routine that does not require heroics.
- The public-facing cadence statement matches what you actually do.

### Phase 6 Implementation Plan (Detailed; sub-phases)

Phase 6 is mostly **operationalizing** what already exists: turning “we do X” into **repeatable routines** with lightweight artifacts so you can prove reliability over time without burning out.

Key principle: Phase 6 is successful when it reduces cognitive load and removes “heroic memory” from operations.

#### Sub-phase 6A — Baseline audit (inventory + decisions)

Goal: ensure the cadence/scope language you publish is true, and ensure the internal routines match the system as deployed.

Tasks:

- Inventory current reality (don’t guess):
  - What cadence you actually run today (annual editions + ad-hoc).
  - Which timers/automation are installed and enabled on the VPS (if any).
  - What backups exist and how restore is validated today.
  - What monitoring exists today (external checks, Healthchecks-style pings, logs).
- Decide what you are comfortable committing to publicly (policy that matches your capacity):
  - Annual edition is the default (“Jan 01 UTC”).
  - What counts as an “ad-hoc capture” exception (e.g., major event, urgent operational fix).
  - Whether annual scheduling should be fully automated or remain operator-triggered.
- Decide whether “replay automation” is enabled and what the retention stance is (WARCs must remain available if replay is enabled).

Deliverables:

- A short “Phase 6 baseline notes” section appended to the changelog (or a dated internal note) capturing the decisions above.

Acceptance criteria:

- You can state “what happens when” in one paragraph without contradictions.

#### Sub-phase 6B — Public capture cadence policy (public-facing)

Goal: publish a clear, non-misleading cadence statement that matches annual-edition reality and avoids “real-time update” impressions.

Tasks:

- Update public copy to be explicit and consistent:
  - “Annual edition captured Jan 01 UTC.”
  - “Exceptions may trigger ad-hoc captures; these are explicitly labeled.”
  - “Change tracking is edition-aware; it does not imply real-time monitoring.”
- Ensure the policy appears where users form expectations:
  - `/methods` (primary place for operational details),
  - `/governance` (policy + scope stance),
  - `/changes` + `/digest` (short, high-visibility summary),
  - optionally `/status` (freshness definitions).

Deliverables:

- Public cadence policy text (short, stable, linked from the footer or methods/governance).

Acceptance criteria:

- A user cannot reasonably interpret “Changes” as “this is what happened this week in real time” when editions are annual.

#### Sub-phase 6C — Ops cadence (internal runbook routines)

Goal: define a boring, repeatable operations cadence that can be followed by you or a backup operator.

Recommended cadence (adjust to your capacity):

- Weekly: 10–15 minute health review
  - API health, worker status, disk usage trend, failed jobs queue.
- Monthly: reliability review (can be merged into the monthly impact report)
  - top incidents, planned maintenance, search quality checks.
- Quarterly: restore test (prove backups are usable)
  - restore DB + verify key endpoints or counts.
- Ongoing: dependency patching routine
  - safe update cadence; avoid surprise upgrades during capture campaigns.

Tasks:

- Write a single “Ops cadence checklist” doc that includes:
  - what to check,
  - where to look (systemd, logs, DB counts),
  - how to record outcomes (a lightweight log entry).
- Decide where the “ops log” lives:
  - simplest: a dated Markdown file in a private operator notes location (not necessarily in git),
  - or, if public-safe, a minimal “incident log” section on `/impact` or `/changelog`.

Deliverables:

- Internal ops cadence doc (checklist-style, copy/paste commands, no secrets).

Acceptance criteria:

- A future-you can follow the checklist after 3 months away and still operate safely.

#### Sub-phase 6D — Automation gates (systemd timers + safe enablement)

Goal: enable automation only where it reduces toil without increasing risk.

Tasks:

- Validate installed timers and dry-run services (safe-by-default):
  - annual scheduling dry-run,
  - change tracking dry-run,
  - replay reconcile dry-run (if replay enabled),
  - annual search verification (optional).
- Decide which automations to enable now vs later:
  - **Change tracking:** typically safe to keep enabled (already capped and edition-aware).
  - **Annual scheduling:** enable only after confirming job configs, disk headroom, and monitoring.
  - **Replay reconcile:** enable only if replay is enabled and stable.
- Use sentinel files to gate automation:
  - `/etc/healtharchive/automation-enabled` (annual scheduling),
  - `/etc/healtharchive/change-tracking-enabled`,
  - `/etc/healtharchive/replay-automation-enabled` (optional).
- Add “timer ran” visibility (optional but high leverage):
  - Healthchecks-style pings using a root-owned env file on the VPS (no URLs in git).

Deliverables:

- Updated deployment docs indicating which timers are expected to be enabled in production and why.
- A clear operator “enable automation” checklist (dry-run → enable timer → confirm next run).

Acceptance criteria:

- Automation does not surprise-run expensive tasks without an explicit enablement step.

#### Sub-phase 6E — Growth constraints (budgets + boundaries)

Goal: prevent slow “scope creep” from breaking reliability (single-VPS reality).

Tasks:

- Define and publish (internally) budgets with conservative defaults:
  - storage budget (WARCs + DB + backups),
  - source cap per year (how many new sources you can safely add),
  - performance budget (acceptable API latency and indexing overhead),
  - replay retention stance (WARCs must remain if replay is on).
- Make the public-facing version “principle based”:
  - “reliability over breadth,”
  - “scope is constrained by storage and operational capacity,”
  - “sources are added deliberately with explicit rules.”

Deliverables:

- Internal “budgets & constraints” doc (numbers and targets).
- Public-facing summary paragraph in `/governance` (principles, not sensitive numbers).

Acceptance criteria:

- You can answer “why not add 50 sources?” with a documented policy, not vibes.

#### Sub-phase 6F — Disaster recovery proof (restore test discipline)

Goal: make “we have backups” verifiable by doing quarterly restore tests and recording results.

Tasks:

- Define the quarterly restore test procedure:
  - restore DB dump to a temporary/staging DB,
  - run a minimal verification checklist (counts + key endpoints),
  - verify that the system starts cleanly with restored state.
- Create a public-safe template for recording results (no secrets):
  - date, operator, what was restored, what checks ran, pass/fail, follow-ups.

Deliverables:

- Restore test template + documented procedure.

Acceptance criteria:

- You can show “restore tests performed” as a reliability artifact (even if the underlying logs live privately).

---

## Appendix A — Suggested new public routes (conceptual)

These are the most likely frontend routes to be added as part of the roadmap:

- `/governance`
- `/terms`
- `/privacy`
- `/changelog`
- `/report` (issue intake)
- `/status` (status/metrics)
- `/changes` (changes feed)
- `/digest` (digest index + archive)
- `/page/<id-or-encoded-group>` (page timeline; exact URL design is a later decision)
- `/compare?...` (compare view; exact URL design is a later decision)

Note: the exact routing and URL formats should be designed for stability and citation friendliness.

## Appendix B — Monthly impact report template (copy)

Suggested structure (one page):

- Summary: what HealthArchive is, what’s new this month
- Coverage: sources tracked, snapshots/pages totals, major additions
- Reliability: uptime, incidents, crawl/index success notes
- Change tracking: biggest changes (once Phase 3 exists)
- Distribution: partner highlights, mentions/citations
- Roadmap: what’s next (one paragraph)

---

## Appendix C — Status/Metrics Page: Data Sources (mapping)

This appendix makes the Phase 2 “Status/Metrics page” concrete by mapping each suggested display element to existing data sources, so an implementer doesn’t invent new backend endpoints prematurely.

**Data sources that are safe for the public frontend to call:**

- `GET /api/health`
  - Use for “API is up / degraded” and basic counts health.
  - Caveat: it’s a point-in-time check, not uptime history.
- `GET /api/stats`
  - Use for top-level counts: snapshots, pages, sources, latest capture date.
- `GET /api/sources`
  - Use for per-source coverage windows and record counts.
  - Also provides entry points and preview URLs when replay/previews are enabled.

**Data sources that must not be used from the public frontend:**

- `/api/admin/**` and `/metrics` (admin token-protected; reserved for operators).

**Recommended “status page” sections and where the data comes from:**

- **Current status:** `/api/health` status + a plain-language summary of what that means.
- **Coverage totals:** `/api/stats` + (optionally) derived counts from `/api/sources`.
- **Per-source coverage table:** `/api/sources` fields:
  - record count
  - first capture date
  - last capture date
  - entry browse URL (if replay enabled)
  - entry preview URL (if preview cache enabled)

**Uptime history note:**

If you want “99.9% uptime last 30 days,” it should come from an external monitor (UptimeRobot/Healthchecks/etc.) or a deliberately designed internal metric. Don’t invent uptime from `/api/health` alone.

---

## Appendix D — Changelog: Template + Example Entries

Keep the changelog boring and structured. Suggested fields per entry:

- Date (YYYY-MM-DD)
- Category tags (examples: `scope`, `governance`, `ui`, `search`, `replay`, `ops`, `reliability`, `data`)
- “What changed” (2–6 bullets)
- “Why it changed” (1–2 bullets)
- “Notes / limitations” (optional)

Example entry (scope + UI):

- 2026-02-01 — `scope`, `ui`
  - What changed: Added CIHR as a tracked source; improved “archived, not current guidance” banner on browse pages.
  - Why: Expand annual campaign scope within single-VPS limits; reduce misinterpretation risk in browse mode.
  - Notes: CIHR capture coverage is early-stage; some interactive content may not replay fully.

Example entry (reliability + ops):

- 2026-03-01 — `ops`, `reliability`
  - What changed: Added monthly search-verification artifact generation; clarified restore test cadence in ops docs.
  - Why: Improve verifiability and reduce “silent failure” risk.

---

## Appendix E — “Report an Issue”: Categories + Fields

This is intentionally designed to be implementable as a simple page + email/GitHub workflow first.

**Suggested report categories (keep it small):**

- Broken snapshot viewer (raw HTML fails, replay fails, blank iframe)
- Wrong metadata (source, capture date, title, URL)
- Missing content (expected page not present / coverage gap)
- Corrections request (labeling, context, clarification)
- Takedown / opt-out request
- Suggest a source / scope request

**What info to ask for (so reports are actionable):**

- Snapshot link (preferred) or snapshot ID
- Original URL
- What you expected to see vs what happened
- Screenshot (optional)
- Contact email (optional)
- “This is urgent because…” (optional)

**Response expectations (examples):**

- Acknowledge within 7 days.
- Urgent safety labeling issues: acknowledge within 48 hours.
- If not fixable (capture didn’t include required assets), respond with a clear explanation.

---

## Appendix F — Governance Page: Recommended Outline

This is a suggested structure for `/governance` that is “public-interest service” readable but still accurate to the implementation.

1) **Mission**
   - 2–3 sentence mission statement.
   - Explicit “not medical advice / not current guidance / independent project.”

2) **Scope**
   - What sources are currently included (list source codes and names).
   - What is explicitly out of scope (private/user-submitted content, PHI, unrelated domains).
   - Why scope is constrained (reliability, storage, single-VPS reality).

3) **How captures work (high-level)**
   - Time-stamped crawls produce standards-based web archive formats (WARCs).
   - Snapshots are indexed into a database to support search and citation.
   - Replay (if enabled) provides higher fidelity browsing but depends on captured assets.

4) **Provenance commitments (what the project guarantees)**
   - Capture timestamp and original URL are displayed.
   - Snapshots are tied back to WARC-backed storage.
   - Content hashes exist to detect change (at minimum, internal integrity checks).

5) **Corrections**
   - What can be corrected (metadata, labeling, broken links, UX bugs).
   - What cannot always be corrected (missing assets in the captured WARC).
   - Response expectations and how to submit.

6) **Takedown / opt-out**
   - Who can request (rights holders, site owners, etc.).
   - What you need to evaluate a request.
   - Possible outcomes (remove access, limit distribution, add context).

7) **Transparency**
   - Aggregate reporting: number of takedown requests, number of correction requests, general outcomes.
   - Link to changelog and monthly impact reports.

8) **Advisory**
   - Advisory charter + cadence + published members (with permission).

9) **Contact**
   - Link to `/report` and `/contact`.

---

## Appendix G — Change Tracking: Implementation Decisions to Resolve (before building)

The Phase 3 work can balloon if decisions aren’t made early. This list is meant to force clarity before coding.

**Core questions**

1) What is the unit of change?
   - Page group (`normalized_url_group`) is the natural unit, because it already exists and supports “timeline” thinking.

2) What snapshots are compared?
   - “Latest vs previous” per page group is a simple default.
   - Consider how to handle missing/failed captures and non-2xx results.

3) What constitutes a “meaningful change”?
   - Minimal viable: content hash changed → record as “changed”.
   - Next: generate a noise-reduced text diff and highlight changed sections.

4) Where is diff generation run?
   - Must be off the user request path.
   - Should be scheduled or triggered after indexing, with throttling and failure handling.

5) How will you label confidence/noise?
   - Some pages will produce noisy diffs; the UI should communicate that.

**Outputs you should be able to support**

- Page timeline: list of capture points for a page group
- Compare view: two selected captures with a descriptive diff
- Changes feed: list of recent change events
- Digest: weekly view of top changes + “coverage notes”

---

## Appendix H — Digest (Web + RSS): Content Structure Example

Keep it simple, descriptive, and consistent week-to-week.

Suggested weekly digest sections:

- **Summary**
  - “During this period, X pages changed across Y sources.”
  - “Z new pages were discovered; W pages were removed/redirected.”
- **Top changes**
  - A list of the top N changes with:
    - source
    - page title / URL
    - capture dates compared
    - “changed sections” count or “diff size” proxy
    - link to compare view
- **New pages**
  - Pages that appeared for the first time in the archive (new page group)
- **Removed/redirected pages**
  - Pages that disappeared or became unreachable (if detectable)
- **Coverage notes**
  - “PHAC crawl had partial capture of X path; expect gaps.”

RSS guidance:

- Keep RSS item titles descriptive and non-editorial (“PHAC: <page title> changed (2026-02-01 → 2026-02-08)”).
- Include the “not medical advice / not current guidance” disclaimer in the RSS item body or feed description.
