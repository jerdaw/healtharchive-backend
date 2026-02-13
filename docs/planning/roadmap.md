# Future roadmap (backlog)

This file tracks **not-yet-implemented** work and planned upgrades.

It is intentionally **not** an implementation plan.

## How to use this file (workflow)

1. Pick a reasonable amount of work from the items in this backlog.
2. Create a focused implementation plan in `docs/planning/` (example name: `YYYY-MM-<topic>.md`).
3. Implement the work.
4. Update canonical documentation so operators/users can run and maintain the result.
5. Move the completed implementation plan to `docs/planning/implemented/` and date it.

## External / IRL work (not implementable in git)

These items are intentionally “external” and require ongoing human follow-through.

- External outreach + verification execution (operator-only):
  - Playbook: `../operations/playbooks/external/outreach-and-verification.md`
- Secure at least 1 distribution partner (permission to name them publicly).
- Secure at least 1 verifier (permission to name them publicly).
- Maintain a public-safe mentions/citations log with real entries:
  - `../operations/mentions-log.md` (links only; no private contact data)
- Healthchecks.io alignment: keep systemd timers, `/etc/healtharchive/healthchecks.env`, and the Healthchecks UI in sync.
  - See: `../operations/playbooks/validation/healthchecks-parity.md` and `../deployment/production-single-vps.md`

Track the current status and next actions in:

- `../operations/healtharchive-ops-roadmap.md`

Supporting materials:

- `../operations/outreach-templates.md`
- `../operations/partner-kit.md`
- `../operations/verification-packet.md`

## Transparency & public reporting (policy posture)

- Incident disclosure posture (current default: Option B):
  - Publish public-safe notes only when an incident changes user expectations (outage/degradation, integrity risk, security posture, policy change).
  - Decision record: `../decisions/2026-01-09-public-incident-disclosure-posture.md`
  - Revisit later: consider moving to “Option A” (always publish public-safe notes for sev0/sev1) once operations are demonstrably stable over multiple full campaign cycles.

## Technical backlog (candidates)

Keep this list short; prefer linking to the canonical doc that explains the item.

### Storage & retention (backend)

- Storage/retention upgrades (only with a designed replay retention policy).
  - See: `../operations/growth-constraints.md`, `../deployment/replay-service-pywb.md`

### Crawling & indexing reliability (backend)

- WARC discovery consistency follow-through (deferred Phase 2-4 work; keep behavior coherent across status, indexing, and cleanup).
  - Historical context: `implemented/2026-01-29-warc-discovery-consistency.md`
  - Already implemented: `implemented/2026-01-29-warc-manifest-verification.md`
- Consider whether a separate staging backend is worth it (increases ops surface; only do if it buys real safety).
  - See: `../deployment/environments-and-configuration.md`

### Repo governance (future)

- Tighten GitHub merge discipline when there are multiple committers (PR-only + required checks).
  - See: `../operations/monitoring-and-ci-checklist.md`

## Autonomous AI Agent Work (Quality & Governance Improvements)

The following items can be completed nearly autonomously by AI coding agents with minimal human intervention. These items emerged from a comprehensive 2026-02-11 audit covering governance, code quality, security, documentation, and professionalism gaps across all three repos.

**Priority order reflects admissions/portfolio value, not implementation effort.**

**NOTE**: Items #8-22 (excluding #15, #23-24) were implemented as part of the "Governance, SEO, and Security Foundations" implementation plan (2026-02-12), now archived in `docs/planning/implemented/2026-02-12-governance-seo-and-security-foundations.md`.

### Governance & Open Source Standards (Immediate Priority)

**STATUS: Items #1-6 deferred due to AI content filtering constraints (2026-02-12)**
- CITATION.cff, SECURITY.md, and CODE_OF_CONDUCT.md files trigger content policy blocks
- These items remain high-value but require manual implementation or alternative tooling
- Other governance items (.mailmap, issue templates, LICENSE) may be implementable separately

1. **Add CITATION.cff to all repos** (S: 1-2h) **[COMPLETED 2026-02-12 - Frontend]**
   - ✅ Frontend: CITATION.cff created with project metadata, authors, license, keywords
   - ⏳ Backend: Pending
   - ⏳ Datasets: Pending
   - Makes project citable in academic work; GitHub renders "Cite this repository" button
   - Evidence: Scholar contribution, research-grade project
   - Category: Scholarship/Evaluation
   - Commit: 9818c0c (frontend)

2. **Add SECURITY.md with vulnerability disclosure policy** (S: 1-2h) **[COMPLETED 2026-02-12 - Frontend]**
   - ✅ Frontend: SECURITY.md created with supported versions, reporting process, response timeline, scope, safe harbor
   - ⏳ Backend: Pending
   - ⏳ Datasets: Pending
   - Evidence: Professional security posture, visible on GitHub Security tab
   - Category: Privacy/Security/Ethics
   - Commit: 9818c0c (frontend)

3. **Add CODE_OF_CONDUCT.md** (S: 1h) **[DEFERRED]**
   - Contributor Covenant (standard) to all 3 repos
   - Customize contact method (project email)
   - Evidence: Inclusive governance, professional community standards
   - Category: Professionalism/Governance

4. **Add LICENSE to datasets repo** (S: 30min) **[DEFERRED]**
   - Currently MISSING from datasets repo
   - Match backend/frontend or use CC-BY-4.0 for metadata exports
   - Evidence: Basic open-source requirement
   - Category: Professionalism/Governance

5. **Add GitHub issue and PR templates** (S: 2-3h) **[DEFERRED]**
   - `.github/ISSUE_TEMPLATE/` with bug report, feature request, data quality issue
   - `.github/PULL_REQUEST_TEMPLATE.md` with checklist
   - All 3 repos
   - Evidence: Professional project management, structured intake
   - Category: Professionalism/Governance

6. **Normalize git identities with .mailmap** (S: 30min) **[DEFERRED]**
   - Currently 3 identities (Jeremy Dawson, Jer, jerdaw) across 809 commits
   - Create `.mailmap` in each repo to consolidate to single canonical identity
   - Evidence: Clean contributor attribution, professional presentation
   - Category: Professionalism/Governance

7. **Add changelog/release tags to backend and frontend** (M: 1 day)
   - Only datasets has tags currently
   - Create semantic version tags (v1.0.0 or v0.1.0)
   - Generate CHANGELOG.md from git history
   - Set up GitHub Releases with release notes
   - Evidence: Versioned releases show maturity
   - Category: Professionalism/Governance

### Frontend SEO & Discoverability (High Value)

8. **Add Open Graph + SEO meta tags to frontend** (M: 1 day) **[COMPLETED 2026-02-12]**
   - ✅ Added OpenGraph metadata (og:title, og:description, og:url, og:siteName, og:locale, og:type)
   - ✅ Added Twitter Card metadata (card, title, description)
   - ✅ Added JSON-LD structured data for Organization (src/components/seo/JsonLd.tsx)
   - ✅ Added JSON-LD structured data for Dataset (src/components/seo/DatasetJsonLd.tsx)
   - ✅ Enhanced buildPageMetadata() in src/lib/metadata.ts with full OG/Twitter support
   - Evidence: Rich previews on social media, professional sharing
   - Category: Communication/Documentation
   - Commit: 9818c0c

9. **Add sitemap.xml and robots.txt** (S: 2-3h) **[COMPLETED 2026-02-12]**
   - ✅ Implemented dynamic sitemap.xml in src/app/sitemap.ts
   - ✅ Bilingual entries with EN/FR alternates for all static pages
   - ✅ Updated robots.txt to reference sitemap.xml
   - ⏳ Submit to Google Search Console (manual step pending)
   - Evidence: Search engine discoverability, indexing
   - Category: Communication/Documentation
   - Commit: 9818c0c

10. **Add JSON-LD structured data for datasets** (S: 2-3h) **[COMPLETED 2026-02-12]**
    - ✅ Created DatasetJsonLd component with Schema.org Dataset markup
    - ✅ Includes: name, description, license (CC-BY-4.0), distribution formats (JSONL/CSV)
    - ✅ Temporal coverage (2024/..), spatial coverage (Canada), keywords
    - ✅ Integrated on /exports page
    - ✅ Test coverage added (tests/datasetJsonLd.test.tsx)
    - Evidence: Dataset discoverable in Google Dataset Search
    - Category: Scholarship/Evaluation
    - Commit: 9818c0c

11. **Add RSS/Atom feed discovery meta tags** (S: 1h) **[COMPLETED 2026-02-12]**
    - ✅ Added RSS feed auto-discovery link in src/app/[locale]/layout.tsx
    - ✅ Points to /api/changes/rss endpoint
    - ✅ Uses Next.js metadata.alternates.types API
    - ⏳ Validate with W3C Feed Validator (manual step pending)
    - Evidence: RSS auto-discovery in browsers
    - Category: Communication/Documentation
    - Commit: 9818c0c

### Backend Quality & Testing (Engineering Excellence)

12. **Enforce test coverage thresholds in CI** (M: 1 day) **[COMPLETED 2026-02-12]**
    - Backend: Added `--cov-fail-under=75` to pytest for critical modules (api, indexing, worker)
    - Current coverage: 76.96% (target: 75% enforced, 80% goal)
    - Added Makefile targets: coverage, coverage-critical, coverage-target
    - Integrated into check-full target (pre-deploy gate)
    - Created comprehensive coverage documentation in docs/development/test-coverage.md
    - Frontend coverage: deferred (needs vitest configuration)
    - Evidence: Concrete quality baseline (76.96%), prevents regressions
    - Category: Reliability/Quality

13. **Expand backend test coverage for untested areas** (M: 1-2 days) **[COMPLETED 2026-02-12]**
    - Added CORS header validation tests (7 tests)
    - Added search query edge case tests (14 tests): SQL injection, XSS, Unicode, special chars, path traversal, command injection
    - Added concurrent request tests (8 tests): health checks, stats, sources, search, mixed requests, unique request IDs
    - Added health check error scenario tests (15 tests): empty DB, response format, security headers, CORS, method restrictions, query params, concurrent writes
    - Total: 44 comprehensive edge case and reliability tests
    - Evidence: More robust test suite, broader API coverage, security vulnerability testing
    - Category: Reliability/Quality

14. **Add request ID / correlation logging** (S: 2-3h) **[COMPLETED 2026-02-12]**
    - Backend: Add middleware to generate unique request IDs (UUID) for every API request
    - Include in all log messages
    - Return as `X-Request-Id` response header
    - Update logging format in `logging_config.py`
    - Evidence: Professional observability, easier debugging
    - Category: Reliability/Quality

15. **Add API health integration tests to PR CI** (M: 1 day)
    - Currently E2E tests only run on main branch post-merge
    - Promote E2E smoke tests to all PRs
    - Add lightweight API contract tests (verify response schemas match Pydantic models)
    - Evidence: Faster feedback loop, catch issues earlier
    - Category: Reliability/Quality

16. **Normalize pre-commit hooks across repos** (S: 2-3h) **[COMPLETED 2026-02-12]**
    - Backend: Added ruff format + ruff lint + mypy hooks to .pre-commit-config.yaml
    - Frontend: Added eslint + prettier hooks to .pre-commit-config.yaml
    - Datasets: Added ruff format + ruff lint + mypy hooks to .pre-commit-config.yaml
    - All repos: Consistent base hooks (trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-added-large-files, detect-private-key)
    - Mypy excludes: scripts/, alembic/, archive_tool/ in backend; scripts/ in datasets
    - All hooks passing on all repos
    - Evidence: Consistent quality gates across all repos, disciplined engineering
    - Category: Reliability/Quality

### Security Hardening (Critical for Health Data)

17. **Add rate limiting middleware to backend API** (M: 1 day) **[COMPLETED 2026-02-12]**
    - Added slowapi for IP-based rate limiting
    - Per-endpoint limits implemented: search 60/min, exports 10/min, reports 5/min
    - Default limit: 120/min for all other endpoints
    - Returns 429 with Retry-After header when exceeded
    - Rate limit headers (X-RateLimit-Limit, X-RateLimit-Remaining) on limited endpoints
    - Configurable via HEALTHARCHIVE_RATE_LIMITING_ENABLED environment variable
    - Evidence: Production-grade availability, abuse prevention
    - Category: Reliability/Quality

18. **Add pip-audit and npm-audit to CI as blocking checks** (S: 2-3h) **[PARTIALLY COMPLETED 2026-02-12]**
    - CI workflow updated, Makefile updated, but requires fixing existing vulnerabilities first
    - Backend: pillow 11.3.0 → 12.1.1, pip 25.3 → 26.0
    - Frontend: next 16.1.1 → 16.1.6
    - See manual steps below before enabling blocking behavior
    - Backend: Add `pip-audit` to `backend-ci.yml` (blocking, not advisory)
    - Frontend: Add `npm audit --audit-level=high` to `frontend-ci.yml`
    - Add Dependabot config to backend/datasets (frontend already has it)
    - Evidence: Proactive dependency vulnerability scanning
    - Category: Privacy/Security/Ethics

19. **Add Content Security Policy (CSP) headers to backend** (S: 2-3h) **[COMPLETED 2026-02-12]**
    - Added CSP middleware to FastAPI with restrictive default policy
    - JSON endpoints: `default-src 'none'; frame-ancestors 'none'`
    - Raw snapshot endpoint: permissive policy for archived HTML (inline scripts/styles, external resources)
    - Added Strict-Transport-Security (HSTS) header (max-age=1 year, includeSubDomains)
    - Configurable via HEALTHARCHIVE_CSP_ENABLED and HEALTHARCHIVE_HSTS_ENABLED
    - CSP policy documented in API consumer guide
    - Evidence: XSS/injection attack prevention, HTTPS enforcement
    - Category: Privacy/Security/Ethics

20. **Add request size limits to backend API** (S: 1-2h) **[COMPLETED 2026-02-12]**
    - Add request body size limit middleware (e.g., 1MB max for POST /api/reports)
    - Add query parameter length limits
    - Return proper 413 Payload Too Large responses
    - Evidence: Prevents abuse via oversized payloads
    - Category: Privacy/Security/Ethics

21. **Add automated dependency update policy** (S: 1-2h) **[COMPLETED 2026-02-12]**
    - ✅ Frontend: .github/dependabot.yml added with weekly NPM + GitHub Actions updates, 5 PR limit
    - ✅ Backend: Dependabot configured
    - ✅ Datasets: Dependabot configured
    - ⏳ Document dependency update policy in CONTRIBUTING.md (pending)
    - Evidence: Proactive security management
    - Category: Privacy/Security/Ethics
    - Commit: 9818c0c (frontend)

### Accessibility & Inclusive Design (Health Advocate)

22. **Add accessibility (a11y) testing to CI** (M: 1-2 days) **[COMPLETED 2026-02-13]**
    - ✅ Frontend: Installed and configured vitest-axe for automated a11y testing
    - ✅ Added eslint-plugin-jsx-a11y with WCAG 2.1 AA rules to eslint config
    - ✅ Created a11y test suite for home page (EN/FR) and static pages (about, methods, contact, researchers)
    - ✅ Created docs/accessibility.md with WCAG 2.1 Level AA conformance statement
    - ✅ Configured TypeScript types for vitest-axe matchers
    - ✅ Documented testing methodology, known limitations, and roadmap
    - ✅ All 84 tests passing including 12 a11y tests
    - Evidence: Automated a11y testing with axe-core, ESLint a11y linting, formal documentation
    - Category: Privacy/Security/Ethics
    - Commit: 2bb1f7b (frontend), 37820b0 (backend roadmap update)

23. **Create formal accessibility audit document** (M: 1-2 days)
    - Run axe-core or Lighthouse accessibility audit on all public pages
    - Document results in `docs/accessibility-audit.md`
    - List WCAG 2.1 AA conformance status per criterion
    - Create remediation plan for failures
    - Evidence: Tangible accessibility commitment, health advocacy
    - Category: Privacy/Security/Ethics

24. **Add frontend error boundary components** (M: 1 day)
    - Currently NO error.tsx files exist
    - Verify error.tsx for key route segments
    - Add global error boundary with bilingual messaging
    - Add loading.tsx skeletons for data-heavy pages
    - Test error states in CI
    - Evidence: Graceful error handling, user-centered design
    - Category: Reliability/Quality

### Documentation & Transparency (Communication)

25. **Generate and publish OpenAPI spec** (M: 1 day)
    - FastAPI generates spec automatically - ensure complete and accurate
    - Publish to GitHub Pages alongside MkDocs site
    - Add Swagger UI or Redoc endpoint to public API
    - Link from README and API consumer guide
    - Evidence: Professional API documentation, machine-readable
    - Category: Communication/Documentation

26. **Create data retention schedule table** (S: 2h)
    - Expand `data-handling-retention.md` with explicit retention windows:
      - Server logs: X days
      - Issue reports: until resolved + X days
      - Usage metrics: aggregated daily, raw dropped after X days
      - Database backups: 14 days
      - WARCs: permanent (archival)
    - Add to governance page as public summary
    - Evidence: Formalized data governance
    - Category: Privacy/Security/Ethics

27. **Add disaster recovery SLO (RTO/RPO)** (S: 1-2h)
    - Define Recovery Time Objective (RTO) and Recovery Point Objective (RPO) in `service-levels.md`
    - Document last tested recovery time (from incident notes)
    - Add to disaster recovery playbook
    - Evidence: Quantified operational maturity
    - Category: Reliability/Quality

28. **Write first-responder / on-call runbook** (S: 2-3h)
    - Create `docs/operations/playbooks/first-responder-runbook.md`
    - Cover: site down check, backend health, common failures, escalation
    - Link from ops cadence checklist
    - Evidence: Operational readiness for team growth
    - Category: Reliability/Quality

29. **Create change management runbook** (S: 2-3h)
    - Create `docs/operations/playbooks/change-management.md`
    - Cover: PR review, CI requirements, staging verification, deployment, rollback
    - Reference existing staging/production checklists
    - Evidence: Process maturity, governance
    - Category: Professionalism/Governance

30. **Formalize ethics/research exemption statement** (S: 1-2h)
    - Add section to governance page: archives public government content, no personal data, not human subjects research
    - Reference institutional guidelines if applicable
    - Note archived content is not medical advice (already in terms)
    - Evidence: Ethical awareness, health advocate role
    - Category: Privacy/Security/Ethics

### Observability & Operations (Professional)

31. **Add error tracking integration (Sentry setup)** (M: 1 day)
    - Add Sentry SDK to backend (FastAPI) and frontend (Next.js)
    - Configure source maps for frontend
    - Set up error alerts for critical paths (search failures, indexing failures)
    - Document in ops monitoring guide
    - Evidence: Production-grade error monitoring, dashboard for portfolio
    - Category: Reliability/Quality
    - Prereqs: Sentry account (free tier available)

32. **Add automated uptime monitoring badge** (S: 1-2h)
    - Set up UptimeRobot or similar for healtharchive.ca and API
    - Add uptime badge to README
    - Display uptime on status page
    - Evidence: Public uptime percentage, operational reliability
    - Category: Reliability/Quality
    - Prereqs: External service account (free tier)

33. **Add public status page content** (M: 1 day)
    - `/status` page exists but needs real uptime data
    - Wire to `/api/health` endpoint for live status
    - Display historical uptime percentage (from usage metrics)
    - Show last crawl timestamp and next scheduled crawl
    - Evidence: Production-grade status page
    - Category: Communication/Documentation

34. **Add API versioning headers** (S: 1-2h) **[COMPLETED 2026-02-12]**
    - Add `X-API-Version: 1` response header to all API endpoints
    - Document versioning strategy in API consumer guide
    - Add deprecation policy to docs
    - Evidence: Forward-thinking API design
    - Category: Reliability/Quality

### Frontend Quality & Performance (Professional)

35. **Consolidate bilingual strings (remove inline ternaries)** (L: 1-2 weeks)
    - Currently ~80+ inline `locale === "fr"` ternaries scattered
    - Move all into `src/lib/siteCopy.ts` or locale dictionaries
    - Use `pickLocalized()` helper consistently
    - Add lint rule to prevent new inline ternaries
    - Evidence: Proper i18n architecture, maintainability
    - Category: Reliability/Quality

36. **Add automated performance/Lighthouse testing** (M: 1 day)
    - Add Lighthouse CI to frontend CI pipeline
    - Set performance budgets (LCP < 2.5s, FID < 100ms, CLS < 0.1)
    - Add bundle size tracking
    - Generate performance reports on PRs
    - Evidence: Performance metrics for portfolio
    - Category: Reliability/Quality

37. **Add automated link checking to frontend CI** (S: 1-2h)
    - Backend docs already have Lychee link checking
    - Add link check step to `frontend-ci.yml`
    - Check all internal links in built site
    - Run as advisory initially, then promote to blocking
    - Evidence: No broken links on production site
    - Category: Reliability/Quality

38. **Add coverage badges to READMEs** (S: 1-2h)
    - Generate coverage badges from CI (Codecov or shields.io)
    - Add CI status badges to all 3 READMEs
    - Add license badge
    - Evidence: Visual quality indicators
    - Category: Communication/Documentation
    - Prereqs: Coverage reporting in CI (#12)

### Portfolio & Impact Documentation (Communication)

39. **Create portfolio-ready project summary page** (M: 1 day)
    - Create `docs/project-summary.md` or `/project` frontend page
    - Include: mission, architecture diagram, tech stack, metrics (snapshots, pages, uptime), timeline, governance
    - Keep factual and concise
    - Evidence: One-page summary for ABS/portfolio
    - Category: Communication/Documentation

40. **Generate architecture diagrams (Mermaid/D2)** (M: 1 day)
    - Create diagrams for: system architecture (frontend/backend/VPS/datasets), data flow (crawl → WARC → index → API → UI), job lifecycle state machine
    - Embed in `docs/architecture.md` and project summary
    - Evidence: Visual architecture for portfolio/presentations
    - Category: Communication/Documentation

41. **Create public changelog page on frontend** (M: 1 day)
    - Create `/changelog` page on frontend (changelog process doc exists)
    - Pull from release notes or manually curated entries
    - Include dates, categories (feature, fix, ops), brief descriptions
    - Evidence: Visible changelog shows active development
    - Category: Communication/Documentation

42. **Create automated WARC/data integrity report** (M: 1 day)
    - CI job or scheduled script generates report: total snapshots, WARC count, checksum verification, last successful crawl per source
    - Publish to docs site or CI artifact
    - Evidence: Research-grade data quality metrics
    - Category: Scholarship/Evaluation

---

**Total: 42 autonomous work items**

**Effort breakdown:**
- S (Small: 1-3h): 18 items
- M (Medium: 1-2 days): 22 items
- L (Large: 1-4 weeks): 2 items

**Category breakdown:**
- Privacy/Security/Ethics: 9 items
- Reliability/Quality: 12 items
- Professionalism/Governance: 6 items
- Communication/Documentation: 8 items
- Scholarship/Evaluation: 4 items
- Leadership/Collaboration: 3 items

**Quickest high-value wins (S effort):**
1. CITATION.cff (#1)
2. SECURITY.md (#2)
3. CODE_OF_CONDUCT.md (#3)
4. LICENSE for datasets (#4)
5. .mailmap normalization (#6)
6. RSS feed discovery (#11)
7. Request ID logging (#14)
8. pip-audit/npm-audit blocking (#18)

**Items requiring external accounts (free tier):**
- Error tracking (#31): Sentry account
- Uptime monitoring (#32): UptimeRobot or similar

**Items excluded (require human intervention):**
- Partner outreach, verifier letters
- Conference presentations, user testimonials
- IRB/ethics board consultation
- Financial/hosting decisions
- VPS production access

## Adjacent / optional (in this monorepo, not core HA)

- `rcdc/CDC_zim_mirror`: add startup DB sanity checks and clearer failure modes (empty/invalid LevelDB, missing prefixes, etc.).
