# Admissions Strengthening Plan (OMSAS ABS + CanMEDS)

**Created:** 2026-02-25
**Status:** Active
**Scope:** Cross-repo (backend, frontend, datasets) + external/IRL work
**Timeline:** ~12 weeks (~6-8 hours/week, ~65-96 hours total)

---

## Purpose

This plan identifies and prioritizes work that strengthens HealthArchive.ca's
presentation for Ontario medical school admissions (OMSAS ABS + CanMEDS
framework). It was produced by analysing the project's current state, then
subjecting each candidate improvement to both devil's advocate and steelman
evaluation before arriving at a fair, objective synthesis.

The plan does **not** prioritize polish over core functionality. The project's
technical foundation is already production-grade. The primary gaps are external
validation, scholarly outputs, and narrative legibility for non-technical
audiences.

### Methodology note

Each item below survived a three-pass evaluation:

1. **Original assessment:** identified as potentially valuable for admissions.
2. **Devil's advocate:** challenged each item's actual admissions value,
   achievability, and whether it is visible to the relevant audience.
3. **Steelman:** argued the strongest honest case for each item, often
   reframing the target audience or mechanism of value.
4. **Objective synthesis:** weighed both sides fairly to produce a verdict
   and tier assignment.

Items that did not survive scrutiny (Sentry, `.mailmap`, dependency audit
blocking, internal ops items, WARC integrity report) were removed.

---

## Current state summary

- **Purpose:** Independent web archive of Canadian public health web pages
  (Health Canada, PHAC, CIHR) with time-stamped snapshots, edition-aware
  change tracking, bilingual UI, and metadata-only research exports.
- **Maturity:** Production-deployed. Active annual crawls on VPS.
  Sophisticated backend (FastAPI/PostgreSQL), frontend (Next.js/Vercel),
  90+ backend test files, CI/CD, Prometheus/Grafana monitoring,
  Alertmanager, systemd automation, incident logs, DR runbook, and
  architectural decision records.
- **External validation:** Zero. The mentions log is empty. No named
  partners, verifiers, or citations. The quarterly dataset release pipeline
  exists but has no confirmed published releases. Performance baselines
  in the SLO doc are all "TBD."
- **Core gap:** The project is technically impressive but externally
  invisible. The admissions challenge is making the work legible and
  independently verifiable.

---

## Value hierarchy

Not all improvements are equally valuable. The plan follows this hierarchy:

1. **External validation** (verifier, partner, advisor) -- the only evidence
   that exists outside your own project.
2. **Scholarly output** (methods paper, dataset DOI) -- creates citable,
   durable, peer-reviewed artifacts.
3. **Narrative specificity** (real examples, real counts, accessibility work)
   -- makes claims concrete and verifiable.
4. **Hygiene** (badges, CoC, templates, uptime) -- prevents credibility
   leaks; do it, don't celebrate it.
5. **Application writing** (ABS entries, interview prep) -- the translation
   layer that converts all of the above into admissions value.

---

## Phase 1: Start immediately (weeks 1-2)

These items either have long lead times or are quick prerequisites that
unblock everything else.

### 1. Begin external outreach (verifier + partner + advisory input)

*Combines: verifier, distribution partner, advisory circle.*
*Roadmap items: Gate 1 (distribution), Gate 2 (verification), advisory circle.*

**Why it matters:** A named verifier or distribution partner is the single
highest-value artifact for admissions. It is the only form of evidence that
exists entirely outside the applicant's own project.

**What to do:**

- Identify 5-10 contacts. Prioritize warm connections first (professors you
  already know, your institution's health sciences librarian, anyone in your
  network adjacent to public health, journalism, or digital preservation).
- For cold contacts: digital scholarship librarians at Canadian universities,
  researchers who study health communication, health journalism instructors.
- Send personalized outreach using the existing templates
  (`docs/operations/outreach-templates.md`). The ask is lightweight: review
  the project, offer feedback, and potentially serve as a verifier or link
  to it from a resources page.
- Follow the outreach playbook
  (`docs/operations/playbooks/external/outreach-and-verification.md`):
  follow up at 10 days, close the loop at 3 weeks.
- Any contact who engages becomes a potential verifier, partner, or advisor.
  Pursue whichever role fits.

**Evidence created:** Named verifier in ABS entry; public link from a
partner's professional page; advisory feedback documented and acted on.

**Effort:** M (5-8h setup + ongoing follow-up over weeks)

**Dependencies:** Items 1a-1d (below) should be done first so outreach
materials are credible.

### 1a. Fix source count inconsistency and fill in real coverage numbers

*Roadmap items: backlog item #39 (portfolio page, partially).*

**What to do:**

- Confirm actual sources in production (hc, phac, cihr -- or whatever is
  true).
- Pull real counts from `/api/stats`: snapshots, pages, sources, date range.
- Update all public-facing documentation, the verification packet
  (`docs/operations/verification-packet.md`), and the partner kit
  (`docs/operations/partner-kit.md`) with consistent, accurate numbers.

**Evidence:** Consistent, accurate counts across all surfaces; credible
verification packet ready to attach to outreach emails.

**Effort:** S (1-2h)

### 1b. Create or update the portfolio narrative page

*Roadmap item: #39 (portfolio-ready project summary page).*

**What to do:**

- Write or update a non-technical landing page (at `/about` or standalone
  URL) that answers: what is this, why does it matter for health, who is it
  for, what has it done, how is it governed.
- This is the URL included in every outreach email. Target reading level:
  university-educated non-programmer.
- Keep it factual: real counts, real scope, honest about early-stage status.

**Evidence:** A shareable URL for outreach. Not an admissions artifact itself
but a multiplier for all external validation efforts.

**Effort:** S (2-4h)

**Dependencies:** Item 1a (real counts).

### 1c. Set up external uptime monitoring

*Roadmap items: #32 (uptime badge), #33 (status page with history).*

**What to do:**

- Set up UptimeRobot or Freshping on `api.healtharchive.ca/api/health` and
  `healtharchive.ca`.
- Enable the public status page.
- Start now -- value compounds over time as months of monitoring history
  accumulate. By application season, this creates independent, timestamped,
  third-party evidence that the project ran continuously for months before
  you applied.

**Evidence:** An independent, third-party record of sustained operation.
Verifiable by anyone.

**Effort:** S (1h)

### 1d. Publish ethics posture and data retention summary on `/governance`

*Roadmap items: #26 (data retention schedule), #30 (ethics statement).*

**What to do:**

- Add 2-3 paragraphs to the existing `/governance` page covering: what data
  the project collects, how long it is retained, what is never collected,
  and the ethical basis for archiving public government content.
- Frame carefully: "This project archives publicly available government web
  content and does not involve human subjects research. Here is our approach
  to privacy and accountability."
- Do not invoke TCPS 2 by name -- the goal is to show proactive ethical
  reasoning, not to imply regulatory scope the project does not have.
- Do before outreach: a verifier or partner will check `/governance`.

**Evidence:** A public ethics/privacy statement. Removes a friction point
for external validators.

**Effort:** S (2-3h)

---

## Phase 2: Core scholarly output (weeks 2-6)

This is the centerpiece. Protect this writing time above all else.

### 2. Write and publish the methods paper

*Combines: methods paper/preprint, architecture diagrams, expanded /methods
page, transparency counts (included in paper, not standalone).*
*Roadmap item: #40 (architecture diagrams), methods-note-outline.*
*Related doc: `docs/operations/methods-note-outline.md`.*

**Why it matters:** A first-authored, DOI-bearing scholarly output about a
self-initiated project is rare among medical school applicants. Most
applicants have research experience as RA roles -- co-authoring someone
else's paper. A first-authored methods note about infrastructure you
conceived, built, and operate demonstrates a qualitatively different level
of scholarly initiative.

**What to do:**

- Write a 2,000-4,000 word methods note using the existing outline in
  `docs/operations/methods-note-outline.md`.
- Include: introduction (why archiving health guidance matters), system
  overview with architecture diagram (Figure 1), capture methodology,
  change tracking approach, 2-3 real examples of detected changes,
  limitations and failure modes, ethics posture, results (real counts).
- Include honest health equity framing: "The project was motivated by
  preservation and accountability concerns; we recognize it also serves
  equitable access to public health information."
- Create an architecture diagram (Mermaid, rendered via MkDocs Material) as
  part of this work. The Mermaid stub in `docs/project.md` is a starting
  point.
- Target venue: **JOSS** (Journal of Open Source Software) for peer review.
  Post a preprint simultaneously on Zenodo or OSF for immediate DOI.
- After drafting, adapt key sections into an expanded `/methods` page on the
  frontend.

**Evidence:** A DOI-bearing, first-authored scholarly publication. The
strongest single artifact for CanMEDS Scholar.

**Effort:** L (20-30h of writing over 3-4 weeks)

**Dependencies:** Real coverage counts (1a); architecture diagram created as
sub-task; advisory input from outreach (1) improves quality if available.

### 3. Publish first formal dataset release with Zenodo DOI

*Roadmap items: dataset release impact trail, Gate 4 (repeatability evidence).*
*Related doc: `docs/operations/dataset-release-runbook.md`.*

**What to do:**

- Trigger the existing dataset release pipeline to produce a real release
  in `healtharchive-datasets`.
- Verify integrity: `sha256sum -c SHA256SUMS`.
- Enable Zenodo GitHub integration on `jerdaw/healtharchive-datasets` to
  mint a DOI automatically.
- Reference the dataset DOI in the methods paper.
- Add the Zenodo DOI badge to the datasets README.
- Record the release in the claims registry
  (`docs/operations/claims-registry.md`).

**Evidence:** A citable, integrity-verified dataset release with a DOI.
Independently verifiable by anyone.

**Effort:** S-M (3-6h if pipeline works; longer if debugging needed)

**Dependencies:** Backend API must be returning real data.

---

## Phase 3: Supporting evidence and narrative (weeks 4-8)

Do these as Phase 2 items are in progress or landing.

### 4. Accessibility audit and fixes

*Roadmap item: #23 (formal accessibility audit).*

**Why it matters:** Accessibility for a public health resource is a direct
expression of CanMEDS Health Advocate. "I ensured this public health
resource is accessible to Canadians with disabilities" is a narrative point
that almost no other applicant will have.

**What to do:**

- Run Lighthouse and axe-core against key public pages (`/`, `/archive`,
  `/changes`, `/snapshot/[id]`) privately.
- Fix critical and high-severity issues (color contrast, missing alt text,
  keyboard navigation gaps).
- Document what was found and what was fixed.
- Update `/governance` or `/about` with a statement: "We audit accessibility
  regularly and prioritize fixes for users with disabilities."

**Evidence:** A true claim about accessibility work done. Concrete
CanMEDS Health Advocate + Communicator narrative.

**Effort:** M (1-2 days: half-day audit, 1 day fixes, 1h documentation)

### 5. Curate 2-3 real change detection examples

**Why it matters:** Specific, verifiable examples of detected changes
connect the technical work to a concrete public interest outcome. One real
story about detecting a specific health guidance change is worth more than
all architectural documentation combined.

**What to do:**

- Review existing archived snapshots for concrete cases where government
  health content was modified, removed, or restructured.
- Document each with specifics: URL, dates, what changed, why it matters.
- Prioritize examples tied to real events (guideline update, page removal).
- Use these in: methods paper, ABS entry, interview talking points.

**Evidence:** Specific, verifiable examples of the project's public value.
Raw material for every narrative output.

**Effort:** M (4-8h)

**Dependencies:** System must have enough archival history to contain real
changes.

### 6. Batch governance hygiene

*Combines: CODE_OF_CONDUCT, issue/PR templates, CI badges, NLM citation
format, changelog/release tags.*
*Roadmap items: #3, #5, #7, #38.*

**Why it matters:** These items individually have low admissions value but
collectively remove credibility leaks for technical reviewers (librarians,
researchers) evaluating whether to endorse the project. Do them as one batch
session, not as individual milestones.

**What to do:**

- Add `CODE_OF_CONDUCT.md` to all three repos (Contributor Covenant).
- Add issue templates (`Bug Report`, `Feature Request`) and PR template to
  all repos.
- Add CI status badges to all READMEs.
- Add NLM/Vancouver citation format examples to `/cite`.
- Tag current state as v1.0 with honest current date; write a brief
  retrospective changelog going forward. Do not backdate tags.

**Evidence:** Clean GitHub community health score. No negative signals for
technical evaluators. Citation friction removed for medical researchers.

**Effort:** S (3-5h as a single batch)

---

## Phase 4: Application preparation (weeks 8-12)

All prior work is converted into application artifacts.

### 7. Draft OMSAS ABS entries

**Why it matters:** Every other item on this plan only has admissions value
to the extent it can be articulated in the OMSAS ABS format (32-char
activity name, 150-char detail, 900-char significant experience essay).
This is the translation layer that converts technical accomplishments into
CanMEDS-aligned language.

**What to do:**

- Map the project to 2-3 ABS entries:
  - **Research/Scholarly Activity:** Methods paper, dataset release, system
    design and methodology.
  - **Community Service / Health Advocacy:** Public-interest tool for health
    information accountability, stakeholder consultation, accessibility
    work.
  - **Leadership:** Independent initiative, open-source project governance,
    advisory relationships.
- For each entry: activity name, date range, hours, verifier name,
  description within character limits.
- Use specific language: "detected removal of [specific page] during
  [specific event]" not "monitored government websites."
- Map each entry explicitly to CanMEDS roles (Scholar, Health Advocate,
  Communicator, Professional, Collaborator, Leader).
- Reference concrete artifacts: paper DOI, dataset DOI, verifier name,
  public URL.
- Multiple drafts. Have someone else read for clarity.
- Draw on reflective practice notes maintained throughout the process (keep
  a private document logging what you did, what you learned, and which
  CanMEDS role it maps to -- 5-10 minutes after each significant work
  session).

**Evidence:** The actual application.

**Effort:** L (10-15h across multiple drafts)

**Dependencies:** All items in Phases 1-3 provide raw material.

### 8. Interview preparation

**What to do:**

- Prepare a 2-minute plain-language project summary for non-technical
  listeners.
- Prepare 3-4 specific anecdotes mapped to CanMEDS roles (one for Scholar,
  one for Health Advocate, one for Professional, one for
  Collaborator/Leader).
- Practice answering skeptical questions:
  - "Isn't this just a hobby project?" -- point to verifier, paper, dataset,
    partner link.
  - "What was the actual impact?" -- specific change detection examples,
    real counts, external validation.
  - "Why not just use the Wayback Machine?" -- articulate the specific value
    of structured, source-specific, change-tracking archiving for health
    guidance.
- At least 2 mock sessions with a friend or advisor.

**Evidence:** Interview readiness.

**Effort:** M (6-10h)

**Dependencies:** ABS entries (item 7) should be drafted first.

---

## Opportunistic items (do if opportunity arises, don't force)

### 9. Conference presentation or talk

- If a conference, symposium, or seminar series has an open CFP that fits
  the timeline (digital humanities, health informatics, civic tech, library
  science, student research), submit an abstract based on the methods paper.
- If a university library workshop series, data science club, or civic tech
  meetup is looking for speakers, propose a 15-minute talk.
- Do not force this. If no natural venue appears, skip without guilt.

### 10. Restore test documentation

*Roadmap item: restore-test discipline.*

- Complete a restore test following `docs/operations/restore-test-procedure.md`.
- Document in the ops log on the VPS.
- Value: supports interview answer specificity ("I perform quarterly
  documented restore drills") but is not externally visible.

---

## Items removed (with rationale)

These items were evaluated and did not survive the three-pass scrutiny for
admissions purposes. Some remain valuable for project health and should stay
on the ops/technical roadmap, but they are not admissions differentiators.

| Item | Reason |
|---|---|
| Sentry error tracking | Internal ops; existing Prometheus/Grafana monitoring already enables the same interview answers |
| Dependency audit CI blocking | CI config invisible externally; existing SECURITY.md covers the narrative |
| `.mailmap` | Zero admissions value; no reviewer will run `git shortlog` |
| Operational items (lock-dir cutover, mount topology) | Internal infrastructure; belongs on ops roadmap, not admissions plan |
| Automated WARC integrity report | Redundant with dataset release (item 3) for admissions evidence; valuable for ops |
| OpenAPI spec | Already exists via FastAPI; "add one link to README" is not a plan item |

---

## Effort summary

| Phase | Items | Effort | Weeks |
|---|---|---|---|
| Phase 1 (immediate starts) | 1, 1a, 1b, 1c, 1d | ~12-18h | 1-2 |
| Phase 2 (scholarly output) | 2, 3 | ~25-35h | 2-6 |
| Phase 3 (supporting evidence) | 4, 5, 6 | ~12-18h | 4-8 |
| Phase 4 (application prep) | 7, 8 | ~16-25h | 8-12 |
| **Total** | **10 core items** | **~65-96h** | **12 weeks** |

---

## Cross-references

### Roadmap items addressed by this plan

This plan addresses or partially addresses the following items from
`docs/planning/roadmap.md`:

- External / IRL: distribution partner, verifier, mentions log, advisory
  circle
- Real-world validation: Gates 1-4, dataset release, restore test
- Governance: #3 (CODE_OF_CONDUCT), #5 (issue/PR templates), #7
  (changelog/release tags)
- Documentation/ops maturity: #26 (data retention), #30 (ethics statement),
  #32 (uptime badge), #33 (status page history)
- Frontend quality: #23 (accessibility audit), #38 (coverage badges), #39
  (portfolio page), #40 (architecture diagrams)

### Related docs

- Outreach templates: `docs/operations/outreach-templates.md`
- Outreach playbook:
  `docs/operations/playbooks/external/outreach-and-verification.md`
- Verification packet: `docs/operations/verification-packet.md`
- Partner kit: `docs/operations/partner-kit.md`
- Methods note outline: `docs/operations/methods-note-outline.md`
- Claims registry: `docs/operations/claims-registry.md`
- Mentions log: `docs/operations/mentions-log.md`
- Dataset release runbook: `docs/operations/dataset-release-runbook.md`
- Restore test procedure: `docs/operations/restore-test-procedure.md`
