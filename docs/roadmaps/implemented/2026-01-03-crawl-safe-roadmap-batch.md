# Crawl-safe roadmap batch (UX + datasets + governance + outreach) — implementation plan

Status: **completed (repo changes)** (created 2026-01-03; Phase 0–3 completed 2026-01-03; Phase 4 scaffolding completed 2026-01-03; archived 2026-01-03)

Note: This plan is explicitly selected because it is **safe to work on while the annual scrape/crawl is running**:

- No backend crawler changes are required.
- No production VPS restarts or DB migrations are required.
- The only “live” effects are optional: frontend deploys (Vercel) and GitHub Actions changes (datasets release pipeline), which do not touch the running crawl.

If a step would impact production infrastructure (e.g., branch protection rules that block emergency fixes), treat it as **opt-in and reversible**, and schedule it deliberately.

---

## Goal

Deliver four roadmap items (selected from `docs/roadmaps/future-roadmap.md`) with minimal operational risk:

1) **Frontend archive “power controls” (URL-param based)**
- Make advanced archive/search controls clearly discoverable and reliably shareable via URL parameters:
  - `view=pages|snapshots`
  - `includeNon2xx=true`
  - `includeDuplicates=true`

2) **Dataset release pipeline hardening (healtharchive-datasets)**
- Make quarterly dataset releases more reproducible and less flaky by enforcing:
  - retries/backoff that are explicit and diagnosable,
  - manifest validation against the project’s export integrity contract,
  - checksum verification as a required pre-publish step.

3) **Repo governance / merge discipline (future-ready)**
- Define and implement a branch policy that scales from “solo-fast” to “multiple committers” without surprises:
  - required CI checks as deploy gates,
  - PR-only + required checks when/if multiple committers,
  - clear backout procedures to avoid lockouts.

4) **External/IRL outreach + verification**
- Execute a public-safe, privacy-safe outreach workflow to secure:
  - at least 1 distribution partner (permission to name publicly),
  - at least 1 verifier (permission to name publicly),
  - a lightweight mentions/citations log (links only),
  - Healthchecks.io alignment for new/changed timers (ongoing hygiene).

---

## Why this is “next” (roadmap selection)

This batch is intentionally chosen while the annual crawl is running because it:

- Is **implementable without touching** the running crawler/worker.
- Improves the project’s usefulness (UX), defensibility (dataset integrity), and maintainability (governance).
- Uses existing project scaffolding (CI workflows, ops docs, outreach templates) instead of inventing new systems.

---

## Docs setup (do first, before coding)

This repo separates backlog vs implementation plans vs canonical docs to avoid drift
(see `docs/documentation-guidelines.md`).

1) **Create this plan doc**
- File: `docs/roadmaps/implemented/2026-01-03-crawl-safe-roadmap-batch.md` (this document)

2) **Backlog linkage**
- Update `docs/roadmaps/future-roadmap.md` to link the four selected backlog items to this plan, marked “active plan”.

3) **Roadmaps index**
- Update `docs/roadmaps/README.md` to list this plan under “Implementation plans (active)”.

4) **Canonical docs that must remain accurate during/after implementation**
- Governance/CI guidance: `docs/operations/monitoring-and-ci-checklist.md`
- Dataset integrity contract: `docs/operations/export-integrity-contract.md`
- Dataset verification runbook: `docs/operations/dataset-release-runbook.md`
- Frontend implementation guide: `healtharchive-frontend/docs/implementation-guide.md`
- External work templates:
  - `docs/operations/outreach-templates.md`
  - `docs/operations/partner-kit.md`
  - `docs/operations/verification-packet.md`
  - `docs/operations/mentions-log-template.md`

Rule: keep canonical docs describing **what exists and how to use/operate it**; keep this plan
describing **what we will do and in what order**.

---

## Scope, goals, constraints

### In-scope outcomes (what we will deliver)

**Frontend**
- `/archive` exposes and documents “power” controls:
  - `view=pages|snapshots` toggles grouping semantics.
  - `includeNon2xx=true` includes non-2xx captures where supported.
  - `includeDuplicates=true` includes duplicate captures where supported (only meaningful for `view=snapshots`).
- All parameters are:
  - stable (do not silently change meaning),
  - shareable (copy/paste URL reproduces view),
  - localized for English/French UI expectations (EN governs).

**Datasets**
- The datasets publish workflow fails fast on integrity problems:
  - manifest missing required fields,
  - `truncated=true`,
  - checksum verification mismatch,
  - pagination anomalies (non-advancing IDs, repeated IDs).
- Failures are diagnosable:
  - actionable error messages,
  - artifacts/logs captured in GitHub Actions.

**Governance**
- A clear branch policy exists and is enforced appropriately for current reality:
  - “Solo-fast” remains possible without excessive friction.
  - “Multi-committer” mode is pre-designed and can be enabled quickly (PR-only + required checks).

**External**
- A repeatable outreach + verification workflow exists:
  - target list creation, outreach cadence, follow-ups,
  - public-safe documentation/logging,
  - permission capture rules,
  - clear “done” definitions for partner + verifier.

### Non-goals (explicitly out of scope)

- No changes to the crawler (`archive_tool`) or production crawl campaign while it is running.
- No backend DB migrations or API semantic changes as part of this batch.
- No re-architecture of dataset publishing (keep GitHub Releases + JSONL + manifest).
- No “big redesign” of archive UX; keep changes incremental and reversible.
- No storing private contact details in git (ever).

### Constraints to respect (project resources + policy)

- **Small team / solo operator bias**:
  - Prefer simple, reversible changes over heavy process.
  - Do not introduce ongoing maintenance burdens without clear benefit.
- **Public-safety and privacy posture**:
  - Keep outreach artifacts public-safe (no private emails, no sensitive identifiers).
  - Follow `docs/operations/data-handling-retention.md`.
- **Operational stability**:
  - Avoid configuration changes that can lock you out of your repos or block emergency fixes.
  - Prefer staged rollouts for governance changes (warn → enforce).
- **Documentation hygiene**:
  - Keep one canonical doc per concept; link rather than duplicate.

---

## Current-state map (what exists today, to build on)

### 1) Frontend archive “power controls”

Backlog reference: `docs/roadmaps/future-roadmap.md` (“Archive UX (frontend)”).

Observed implementation (audit required):

- `/archive` route: `healtharchive-frontend/src/app/[locale]/archive/page.tsx`
  - Accepts `view`, `includeNon2xx`, `includeDuplicates` as URL params.
  - Treats `includeDuplicates` as meaningful only when `view=snapshots`.
- Supporting component:
  - `healtharchive-frontend/src/components/archive/SearchWithinResults.tsx` (passes through view + filters)
- Existing tests:
  - `healtharchive-frontend/tests/searchWithinResults.test.tsx` (component behavior; not parameter semantics)

Interpretation:

- This backlog item may be **partially or fully implemented already**.
- The work may therefore be: verify semantics + ensure “discoverable + shareable” is actually achieved, then update docs and remove/update the backlog bullet.

### 2) Dataset release pipeline hardening

Backlog reference: `docs/roadmaps/future-roadmap.md` (“Dataset releases”).

Current implementation:

- Publish workflow:
  - `healtharchive-datasets/.github/workflows/publish-dataset-release.yml`
- Release builder:
  - `healtharchive-datasets/scripts/build_release.py`
- Contract + runbook (backend docs, canonical policy):
  - `docs/operations/export-integrity-contract.md`
  - `docs/operations/dataset-release-runbook.md`

Current behavior summary:

- Script paginates exports via `afterId`, writes gzipped JSONL, writes `manifest.json`, writes `SHA256SUMS`.
- Script has basic retries/backoff for HTTP requests, and detects non-advancing pagination.
- Workflow creates or updates a GitHub Release for a date-based tag.

Interpretation:

- The core pieces exist; “hardening” should focus on:
  - stricter validation aligned to the integrity contract,
  - ensuring checksum verification is enforced before publishing,
  - improving flake resilience and diagnostics (without overengineering).

### 3) Repo governance / merge discipline

Backlog reference: `docs/roadmaps/future-roadmap.md` (“Repo governance”).

Current scaffolding:

- CI exists in each repo and is referenced as the deploy gate (“green main”):
  - Backend: `healtharchive-backend/CONTRIBUTING.md` (pre-push hook, `make check`)
  - Frontend: `healtharchive-frontend/CONTRIBUTING.md` (pre-push hook, `npm run check`)
  - Datasets: `healtharchive-datasets/CONTRIBUTING.md` (`make check`)
- CODEOWNERS + PR templates exist for backend/frontend:
  - `healtharchive-backend/.github/CODEOWNERS`
  - `healtharchive-backend/.github/pull_request_template.md`
  - `healtharchive-frontend/.github/CODEOWNERS`
  - `healtharchive-frontend/.github/pull_request_template.md`
- CI + monitoring guidance already exists:
  - `docs/operations/monitoring-and-ci-checklist.md`

Interpretation:

- Governance work is primarily:
  - GitHub repo settings (branch protection),
  - small doc updates (when policy changes),
  - optionally adding missing governance artifacts to datasets repo (CODEOWNERS / PR template).

### 4) External / IRL outreach + verification

Backlog reference: `docs/roadmaps/future-roadmap.md` (“External / IRL work”).

Existing docs/templates:

- Outreach templates: `docs/operations/outreach-templates.md`
- Partner kit (internal guide; points at canonical public assets): `docs/operations/partner-kit.md`
- Verification packet outline: `docs/operations/verification-packet.md`
- Mentions log template: `docs/operations/mentions-log-template.md`
- Adoption signals playbook (quarterly; VPS-stored entries): `docs/operations/playbooks/adoption-signals.md`
- Data-handling constraints (privacy): `docs/operations/data-handling-retention.md`

Interpretation:

- The main “work” here is execution discipline and public-safe recording.
- The plan should standardize:
  - where logs live (VPS vs git),
  - what is allowed to be stored in git,
  - how permissions are tracked without leaking private contact info.

---

## Definition of Done (DoD) + acceptance criteria

### Frontend power controls

1) **Behavior**
- `view=pages|snapshots` always yields consistent grouping behavior.
- `includeNon2xx=true` is preserved through “Search within results” flows and does not get lost on pagination.
- `includeDuplicates=true`:
  - is honored only when `view=snapshots`,
  - is ignored/cleared when `view=pages` (no confusing “enabled but ineffective” state),
  - round-trips through the UI as expected.

2) **Discoverability**
- The `/archive` UI makes it obvious that:
  - there is a “Pages vs All snapshots” view,
  - “Include errors” exists,
  - “Include duplicates” exists (and explains the constraint to snapshots view).

3) **Shareability**
- Copy/pasting the URL reproduces the same view and filters reliably.

4) **Docs**
- The frontend implementation guide documents these query params in the `/archive` section.

### Dataset release pipeline hardening

1) **Integrity enforcement**
- `manifest.json` is validated against `docs/operations/export-integrity-contract.md` before publishing.
- Checksum verification is performed and must pass before publishing.
- `truncated=true` results in a hard failure (no publish).

2) **Reliability**
- Transient network failures do not cause immediate abort; retries/backoff occur with clear logging.
- Workflow avoids accidental double publishes (e.g., concurrency control for scheduled runs).

3) **Diagnostics**
- On failure, the workflow provides:
  - a clear failure reason,
  - enough artifacts/logs to debug without rerunning blindly.

### Repo governance / merge discipline

1) **Policy clarity**
- It is explicit when the project is in “solo-fast” vs “multi-committer” mode.
- The rules are written down in the appropriate canonical doc(s).

2) **Enforcement**
- When “multi-committer” mode is enabled:
  - PR-only merges into `main`,
  - required status checks are enforced,
  - code owners review is required where intended.

3) **Safety**
- There is a tested “backout” procedure for branch protection misconfigurations (avoid lockout).

### External / IRL outreach + verification

1) **Execution**
- At least one distribution partner is secured (permission to name publicly).
- At least one verifier is secured (permission to name publicly).

2) **Public-safe logging**
- Mentions/citations log exists and is maintained with links only; no private contact data.

3) **Ongoing hygiene**
- Healthchecks.io alignment process is defined (what to check, how to keep `/etc/healtharchive/healthchecks.env` in sync with Healthchecks).

---

## Risk register (pre-mortem)

### Governance risks

- **Risk:** Branch protection rules accidentally block urgent fixes or lock out maintainers.
  - Mitigation: stage changes; keep “admin bypass” on initially; document recovery; test on a non-main branch rule first if possible.
- **Risk:** Required checks are flaky → PRs get stuck.
  - Mitigation: fix flakiness first; require only high-signal checks; keep an escape hatch documented.

### Dataset release risks

- **Risk:** Publishing updates an existing tag/release and silently changes a “research object”.
  - Mitigation: define an immutability policy; constrain “allowUpdates” behavior; require explicit operator intent for corrections.
- **Risk:** Export endpoint returns incomplete data (truncation, pagination bug) → dataset release is wrong but published.
  - Mitigation: strict manifest validation; enforce `truncated=false`; verify monotonic IDs; add sanity thresholds.

### Frontend UX risks

- **Risk:** Power controls confuse casual users.
  - Mitigation: sensible defaults; progressive disclosure; small inline help text/tooltips.
- **Risk:** URL params become a “soft API contract” that is later broken.
  - Mitigation: treat params as stable once documented; add tests to prevent regressions.

### Outreach risks

- **Risk:** Accidentally storing private contact info in git.
  - Mitigation: explicit rules; use a private tracker for contacts; keep public logs link-only; review before committing.
- **Risk:** Naming an organization publicly without permission.
  - Mitigation: default to “Pending” and do not name until permission is explicit.

---

## Phase plan (sequential)

This plan is intentionally sequential. Complete each phase (and its verification) before moving to the next.

### Phase 0 — Lock scope + evidence audit (no code changes yet)

Objective: confirm what already exists, define exact deltas, and avoid duplicate/phantom work.

0.1 Inventory the exact backlog bullets covered
- Source: `docs/roadmaps/future-roadmap.md`
- Record the four items and treat them as the only scope for this plan.

0.2 Perform a reality-check audit for each item
- Frontend power controls:
  - Confirm `/archive` supports all target URL params.
  - Confirm controls are visible in the UI when the backend is available.
  - Confirm the params round-trip through:
    - “Apply” form submission,
    - pagination links,
    - “Search within results” flow.
- Dataset pipeline:
  - Confirm current workflow actually performs retries/backoff (in script) and how failures surface in Actions.
  - Confirm whether checksum verification is currently enforced (likely not).
  - Confirm whether manifest fields match `docs/operations/export-integrity-contract.md`.
- Governance:
  - Inventory current GitHub repo settings (out of git):
    - branch protection on `main`,
    - required checks,
    - PR requirements,
    - admin bypass.
- Outreach:
  - Decide where private contact tracking will live (not in git).
  - Decide where public-safe logs will live (git vs VPS).

0.3 Decide what “done” means for already-implemented items
- If frontend power controls are already fully implemented:
  - The work becomes: add missing tests/docs, then remove/update the backlog bullet.
- If dataset pipeline already meets integrity contract:
  - The work becomes: enforce verification in CI/workflow, and improve diagnostics.

Phase exit criteria:
- A short audit note (can be in the PR description later) listing:
  - what is already done,
  - what is missing,
  - what will be changed in the next phases.

#### Phase 0 findings (completed 2026-01-03)

Summary: this batch is viable and crawl-safe. One of the four items (“power controls”) appears **already implemented in code**, and is likely “remaining docs/tests polish” rather than “new UI build”.

**Frontend power controls**

- Current `/archive` URL params already exist and appear wired end-to-end:
  - Route: `healtharchive-frontend/src/app/[locale]/archive/page.tsx`
  - Supported params observed: `view`, `includeNon2xx`, `includeDuplicates` (plus core `q/source/from/to/sort/page/pageSize`).
  - `includeDuplicates` is only applied when `view=snapshots` (both for backend calls and for pagination URL building).
- “Search within results” round-trips these filters through hidden inputs:
  - Component: `healtharchive-frontend/src/components/archive/SearchWithinResults.tsx`
  - Existing test covers basic “reveals and submits” behavior:
    - `healtharchive-frontend/tests/searchWithinResults.test.tsx`
- Likely remaining gaps (to confirm in Phase 3 before changing anything):
  - Documentation: `healtharchive-frontend/docs/implementation-guide.md` does not currently appear to clearly list the `/archive` query params as a stable contract.
  - Regression tests: there is no dedicated test coverage asserting param semantics such as “duplicates only in snapshots view”.

**Dataset release pipeline hardening**

- Baseline functionality exists and already includes some hardening:
  - Publish workflow: `healtharchive-datasets/.github/workflows/publish-dataset-release.yml`
  - Builder script:
    - Retries/backoff exist for JSON fetch and NDJSON streaming.
    - Pagination safety exists (non-advancing IDs fail).
    - Outputs: gzipped JSONL exports + `manifest.json` + `SHA256SUMS`.
    - File: `healtharchive-datasets/scripts/build_release.py`
- Confirmed remaining hardening needs (Phase 2 scope stays valid):
  - Explicit manifest validation aligned to `docs/operations/export-integrity-contract.md`.
  - Explicit checksum verification as a required step before publishing (e.g., `sha256sum -c SHA256SUMS`).
  - Workflow-level hardening such as concurrency control, timeouts, and failure artifacts.
  - Decide/tag immutability posture (workflow currently allows release updates via `allowUpdates: true`).

**Repo governance / merge discipline**

- The “solo-fast” posture and CI-as-deploy-gate are already documented and supported:
  - `healtharchive-backend/CONTRIBUTING.md`
  - `healtharchive-frontend/CONTRIBUTING.md`
  - Canonical policy doc: `docs/operations/monitoring-and-ci-checklist.md`
- CI workflows and stable job IDs exist (see Phase 1 inventory).
- Unknown (operator-only): current branch protection settings in GitHub UI; audit required before changing anything.

**External / IRL outreach + verification**

- Templates and public-safe scaffolding exist:
  - `docs/operations/outreach-templates.md`
  - `docs/operations/partner-kit.md`
  - `docs/operations/verification-packet.md`
  - `docs/operations/mentions-log-template.md`
- Unknown (operator-only): current real-world status (partners contacted, verifier candidates, existing mentions).
- Recommendation for execution hygiene:
  - Keep private contact tracking out of git entirely.
  - Create/maintain a public-safe mentions log only once there are public links (and permission to name).

**Healthchecks alignment (operator-only; completed)**

- Healthchecks.io checks were aligned to enabled timers and `/etc/healtharchive/healthchecks.env` on the VPS.
- Added daily checks for:
  - `healtharchive-annual-search-verify`, `healtharchive-change-tracking`,
    `healtharchive-coverage-guardrails`, `healtharchive-replay-smoke`
- Fixed annual sentinel configuration:
  - `healtharchive-annual-campaign-sentinel` is configured as yearly with sufficient grace.
- Intentional exception:
  - `healtharchive-cleanup-automation` remains disabled and is not monitored in Healthchecks (see `docs/operations/playbooks/healthchecks-parity.md`).

---

### Phase 1 — Repo governance / merge discipline (design + staged enforcement)

Objective: align process to project reality and avoid brittle enforcement that harms velocity.

1.1 Establish two explicit governance modes

**Mode A: Solo-fast (current recommended posture)**
- Direct pushes to `main` permitted.
- CI runs on every push; “green main” is the deploy gate.
- Local hooks are the primary guardrail:
  - backend: `./scripts/install-pre-push-hook.sh` (runs `make check`)
  - frontend: `./scripts/install-pre-push-hook.sh` (runs `npm run check`)

**Mode B: Multi-committer (future posture)**
- PR-only merges into `main`.
- Required status checks enforced by branch protection.
- Code owner review required where appropriate.

Decision trigger (explicit):
- Switch to Mode B when there is more than one regular committer, or when you want stricter enforcement than “social contract + hooks”.

1.2 Document the policy (canonical docs)
- Primary reference: `docs/operations/monitoring-and-ci-checklist.md`
- Ensure it clearly states:
  - what “green main” means,
  - what checks are required,
  - when to switch governance modes,
  - how to recover from a misconfiguration.

1.3 Audit CI check names and stability (per repo)
- Backend:
  - Identify the exact GitHub Actions workflow + job names that should be required in Mode B.
  - Ensure they are stable (renaming breaks branch protection).
- Frontend:
  - Same: identify stable check names.
- Datasets:
  - Identify stable check names from `healtharchive-datasets/.github/workflows/datasets-ci.yml`.

Current check name inventory (as of 2026-01-03; confirm in GitHub UI before locking protections):

- Backend repo:
  - Workflow: `Backend CI` (`healtharchive-backend/.github/workflows/backend-ci.yml`)
  - Jobs:
    - `test` (required check name usually appears as `Backend CI / test`)
    - `e2e-smoke` (required check name usually appears as `Backend CI / e2e-smoke`)
- Frontend repo:
  - Workflow: `Frontend CI` (`healtharchive-frontend/.github/workflows/frontend-ci.yml`)
  - Jobs:
    - `lint-and-test` (required check name usually appears as `Frontend CI / lint-and-test`)
    - `e2e-smoke` (required check name usually appears as `Frontend CI / e2e-smoke`)
- Datasets repo:
  - Workflow: `Datasets CI` (`healtharchive-datasets/.github/workflows/datasets-ci.yml`)
  - Jobs:
    - `lint` (required check name usually appears as `Datasets CI / lint`)

Important:

- Do not rename workflow/job IDs after you enable required checks, or GitHub will treat them as missing.
- Cross-repo e2e checks require a token if the sibling repo is private:
  - Secret: `HEALTHARCHIVE_CI_READ_TOKEN` (documented in `docs/operations/monitoring-and-ci-checklist.md`).

1.4 Implement missing governance artifacts (datasets repo)
- Add `CODEOWNERS` and a PR template to datasets repo if absent, consistent with backend/frontend.
- Ensure CONTRIBUTING covers pre-push hook guidance if you want “solo-fast” there too.

1.5 Stage branch protection changes (operator actions in GitHub UI)

Staged rollout recommended:

- Stage 1 (safe baseline):
  - Protect `main` from force-pushes and branch deletion.
  - Enable required status checks **for PR merges only** (does not block direct pushes).
  - Keep admin bypass enabled initially.
- Stage 2 (enable Mode B):
  - Require PRs to merge into `main`.
  - Require status checks to pass before merge.
  - Optionally require code owner review.
  - Optionally require up-to-date branches (only if it doesn’t create constant rebase churn).

Backout plan (must be written before enabling Mode B):
- Document how to temporarily relax protections if CI is broken or an emergency fix is required.
- Ensure at least one maintainer account can always edit branch protection rules.

Phase exit criteria:
- Policy is documented.
- Datasets repo has parity on governance artifacts (if chosen).
- A checklist exists for turning Mode B on/off without lockout.

#### Phase 1 notes (completed 2026-01-03)

- Policy documentation updated (canonical): `docs/operations/monitoring-and-ci-checklist.md`
  - Adds Mode A vs Mode B framing, includes datasets in “run checks before push”, and records a check-name inventory.
- Datasets repo governance artifacts added:
  - `.github/CODEOWNERS`, `.github/pull_request_template.md`
  - `scripts/install-pre-push-hook.sh` + `CONTRIBUTING.md` pre-push guidance
- Branch protection changes were intentionally **not** applied (solo-dev posture); defer Mode B until there are multiple committers.

---

### Phase 2 — Dataset release pipeline hardening (healtharchive-datasets)

Objective: make releases defensible, reproducible, and less flaky, using the existing contract.

2.1 Convert the integrity contract into executable checks

Source of truth: `healtharchive-backend/docs/operations/export-integrity-contract.md`.

Implement (in datasets repo) a validation routine that asserts at minimum:

- Manifest required fields exist:
  - `version`, `tag`, `releasedAtUtc`, `apiBase`, `exportsManifest`, `artifacts.snapshots`, `artifacts.changes`.
- Export artifacts required fields exist (for both snapshots/changes):
  - `rows`, `minId`, `maxId`, `requestsMade`, `limitPerRequest`, `truncated`, `sha256`, `filename`.
- Invariants:
  - `truncated` is `false` (or else fail hard).
  - `rows` is non-negative; if `rows==0`, treat as suspicious and require explicit operator override.
  - `minId <= maxId` when rows > 0.
  - `requestsMade >= 1` when rows > 0.

2.2 Enforce checksum verification before publish

Hard requirement:

- Before creating/uploading the GitHub Release, run:
  - `sha256sum -c SHA256SUMS` (or Python equivalent on all platforms).

Additionally recommended:

- Smoke-validate gzip integrity:
  - decompress stream (without fully loading into memory),
  - ensure JSONL lines parse and contain required `id_field`.

2.3 Improve retry/backoff and diagnostics without overengineering

Goals:

- Reduce transient flake failures (timeouts, 502/503, temporary network issues).
- Produce logs that explain:
  - which request failed,
  - which attempt number,
  - what the response status/body snippet was (capped).

Recommended improvements:

- Add jittered exponential backoff (avoid thundering herd).
- Special-case retryable HTTP statuses (429, 502, 503, 504).
- Ensure timeouts are sane for large exports:
  - per-request timeout (already exists),
  - possibly a per-export overall timeout via workflow `timeout-minutes`.
- Add a clear summary at the end:
  - row counts, min/max IDs, checksum values.

2.4 Add workflow hardening

In `healtharchive-datasets/.github/workflows/publish-dataset-release.yml`:

- Add `concurrency` to prevent overlapping scheduled runs.
- Set explicit `timeout-minutes` for the publish job.
- Upload `dist/` as an artifact on failure (so you can inspect partial outputs).
- Decide release immutability posture:
  - If tags must be immutable:
    - disable silent updates,
    - or allow updates only with an explicit workflow input “allow_update=true” and only when the previous release is flagged incomplete.

2.5 Update runbooks/docs to match reality

- Ensure `healtharchive-datasets/README.md` and
  `healtharchive-backend/docs/operations/dataset-release-runbook.md` remain accurate:
  - what integrity checks run automatically,
  - what to do on failure,
  - how to verify a release locally (`sha256sum -c`).

Phase exit criteria:
- Dataset publish workflow refuses to publish if validation fails.
- Failures are diagnosable from Actions logs + artifacts.
- Docs accurately reflect the new safeguards.

#### Phase 2 notes (completed 2026-01-03)

- Added release bundle validation (datasets repo):
  - `healtharchive-datasets/scripts/validate_release_bundle.py`
  - Enforces required `manifest.json` fields + invariants (`truncated=false`), verifies artifact SHA-256, and validates gzip integrity.
- Hardened the publish workflow (datasets repo):
  - `healtharchive-datasets/.github/workflows/publish-dataset-release.yml`
  - Adds checksum verification, runs bundle validation before publishing, adds concurrency + timeout, uploads `dist/` on failure, and defaults to immutable tags (updates only via manual dispatch override).
- Updated docs/runbooks:
  - `healtharchive-datasets/README.md`
  - `healtharchive-backend/docs/operations/dataset-release-runbook.md`

---

### Phase 3 — Frontend archive “power controls” (verify + polish + document)

Objective: ensure the advanced archive controls are truly discoverable and reliably shareable.

3.1 Confirm current behavior against the acceptance criteria

Audit checklist (in local dev / review):

- `/archive` with no params defaults to `view=pages`.
- `/archive?view=snapshots` shows “All snapshots”.
- `/archive?includeNon2xx=true`:
  - stays enabled after hitting “Apply”,
  - stays enabled after pagination.
- `/archive?view=snapshots&includeDuplicates=true`:
  - shows “Include duplicates” enabled,
  - persists through “Apply” + pagination.
- Switching from `view=snapshots` to `view=pages` clears the duplicates effect:
  - ideally, the URL also becomes free of `includeDuplicates=true` to avoid misleading share links.

3.2 Fill the gaps (if any)

Potential missing pieces (only do what the audit proves is missing):

- UI discoverability improvements:
  - make the “Show: Pages / All snapshots” control visible in a consistent place,
  - add short inline help for what “duplicates” means,
  - ensure controls appear only when meaningful (or are visibly disabled with an explanation).
- URL canonicalization:
  - keep URLs clean (do not include ineffective flags).

3.3 Add regression tests (frontend)

Add tests that lock in URL-param semantics and prevent regressions, e.g.:

- `ArchivePage` parsing and canonicalization:
  - `view` parsing defaults and validation,
  - `includeDuplicates` only applying in snapshots view,
  - URL round-trip behavior for “Apply” and “Search within results”.

Constraints:

- Avoid adding tests that require a live backend.
- Keep tests deterministic (use existing mocking patterns).

3.4 Document the parameters (frontend docs)

Update `healtharchive-frontend/docs/implementation-guide.md` to include:

- Supported `/archive` query params:
  - `q`, `within`, `source`, `from`, `to`, `sort`, `page`, `pageSize`,
  - `view`, `includeNon2xx`, `includeDuplicates`.
- A short explanation of each, including constraints (duplicates only relevant for snapshots view).

Phase exit criteria:
- UX is discoverable and stable.
- URL params are documented.
- Tests exist to prevent regressions.

#### Phase 3 notes (completed 2026-01-03)

- Added URL canonicalization so `includeDuplicates` is removed automatically when `view=pages` (no-op flag).
  - Implementation: `healtharchive-frontend/src/app/[locale]/archive/page.tsx`
- Improved UI discoverability with an inline tooltip explaining what “Include duplicates” means (snapshots view).
  - Implementation: `healtharchive-frontend/src/app/[locale]/archive/page.tsx`
- Added regression tests covering URL semantics and filter round-trips:
  - `healtharchive-frontend/tests/archive.test.tsx`
  - `healtharchive-frontend/tests/searchWithinResults.test.tsx`
- Documented the stable `/archive` query param contract:
  - `healtharchive-frontend/docs/implementation-guide.md`

---

### Phase 4 — External / IRL outreach + verification (execution playbook)

Objective: run outreach and verification work with a disciplined, privacy-safe workflow.

4.1 Decide where tracking data lives (privacy-first)

Hard rules:

- Never store private contact details in git.
- Public-safe logs may be stored in git *only if* they contain:
  - public links,
  - public organization names (only with permission),
  - no emails/phone numbers/private notes.

Recommended split:

- Private contact tracker (not in git): a local notes file, password manager notes, or a private spreadsheet.
- Public-safe logs:
  - Mentions/citations log: use `docs/operations/mentions-log-template.md` as the format.
  - Adoption signals entries (quarterly): store on VPS under `/srv/healtharchive/ops/adoption/` (see `docs/operations/playbooks/adoption-signals.md`).

4.2 Build a target list (distribution partners + verifiers)

Distribution partner candidates (examples; tailor to reality):

- University libraries “digital scholarship” resource pages.
- Public health methods resource lists.
- Journalism “tools” pages.
- Research group reproducibility toolkits.

Verifier candidates:

- Librarian (digital scholarship / archives),
- Researcher in reproducibility / STS / public health communication,
- Editor/maintainer of a relevant public methods list.

For each candidate, collect privately:

- name, role, organization,
- contact channel,
- why they are a fit,
- which template to use (A/B/C).

4.3 Outreach cadence (execute using templates)

Use `docs/operations/outreach-templates.md`:

- Send initial outreach (Template A/B/C).
- Follow-up at 1 week.
- Final follow-up at 2 weeks.

Record (privately):

- date sent,
- template used,
- outcome (no response / declined / interested / accepted).

4.4 Partner kit usage (public assets)

Reference internal guide: `docs/operations/partner-kit.md`.

Operational checklist:

- Ensure `/brief` and `/cite` pages are accurate and public-safe.
- Prepare screenshots per the checklist in the partner kit guide.
- When a partner agrees to link:
  - confirm permission to name publicly,
  - ask preferred wording (if any),
  - record the public link once it exists.

4.5 Verification workflow

Use `docs/operations/verification-packet.md` as the packet outline:

- Fill metrics from `/status` (public numbers only).
- Provide the verifier a short list of what verification means, and ask for permission to name them publicly.
- Record verification outcome privately and (if permitted) in a public-safe mentions log.

4.6 Mentions/citations log (public-safe)

Create a real mentions log (not just the template) and keep it current:

- Start from `docs/operations/mentions-log-template.md`.
- Add entries only when there is a public link and permission to name (or record “Pending”).

4.7 Healthchecks.io alignment (ongoing hygiene)

Define a quarterly (or per-change) procedure:

- Inventory timers/services that ping Healthchecks.
- Confirm `/etc/healtharchive/healthchecks.env` matches Healthchecks checks.
- Create missing checks when new timers are added.

Canonical refs:

- `docs/deployment/systemd/README.md`
- `docs/deployment/production-single-vps.md`

Phase exit criteria:
- At least one distribution partner and verifier secured (with permission).
- Public-safe mentions log exists and has real entries.
- Healthchecks alignment procedure is documented and followed.

#### Phase 4 notes (scaffolding completed 2026-01-03; execution is operator-only)

- Added an operator playbook to run outreach and verification work in a privacy-safe way:
  - `docs/operations/playbooks/outreach-and-verification.md`
- Created a public-safe mentions log (link-only; do not name without permission):
  - `docs/operations/mentions-log.md`
- Updated ops indexes so the playbook and log are discoverable:
  - `docs/operations/README.md`, `docs/operations/playbooks/README.md`, `docs/operations/ops-cadence-checklist.md`

---

### Phase 5 — Closeout (docs + backlog hygiene)

Objective: ensure the roadmap reflects reality and the work is maintainable.

5.1 Update canonical docs
- Ensure any modified behavior is documented in the canonical locations (frontend guide, ops runbooks, etc.).

5.2 Update the backlog
- In `docs/roadmaps/future-roadmap.md`:
  - remove completed items,
  - or rewrite them into a smaller “next” follow-up item if partial work remains.

5.3 Archive the plan
- When complete, move this plan to:
  - `docs/roadmaps/implemented/` with a dated filename.
- Update `docs/roadmaps/implemented/README.md` and `docs/roadmaps/README.md` accordingly.

Phase exit criteria:
- Backlog is accurate and short.
- This plan is archived as history.

#### Phase 5 notes (completed 2026-01-03)

- Updated backlog (`docs/roadmaps/future-roadmap.md`) so completed items are removed and ongoing operator-only work points to playbooks/logs instead of an “active plan”.
- Archived this plan and updated roadmap indexes (`docs/roadmaps/README.md`, `docs/roadmaps/implemented/README.md`).
