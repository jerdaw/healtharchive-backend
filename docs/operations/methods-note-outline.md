# Methods Note Outline (public-safe)

This outline is designed to become a poster, short preprint, or blog-style methods write-up that is:

- Descriptive (not interpretive).
- Explicitly **not medical advice** and **not current guidance**.
- Reproducibility-focused: “what was published, when, and how we preserve it”.

It is intentionally “outline-first” so it can be quickly adapted to different venues without rewriting the project.

## Working title options

- **HealthArchive.ca: A provenance-first archive of Canadian public health webpages**
- **Preserving temporal provenance in Canadian public health web guidance**
- **From snapshots to auditability: indexing and change tracking for public health webpages**

## One-sentence framing (use everywhere)

HealthArchive.ca preserves time-stamped snapshots of selected Canadian public health webpages so changes remain auditable, citable, and reproducible over time.

## Abstract (structure)

- **Background / problem:** Public health web guidance and surveillance dashboards are “living documents” that can change or disappear; this complicates reproducibility, journalism, and policy history.
- **Objective:** Build an independent, non-authoritative archive that makes historical versions discoverable and citable, with provenance labeling and descriptive change tracking.
- **Methods:** Automated capture → WARC storage → indexing into a searchable database → snapshot viewer + optional replay → edition-aware change tracking.
- **Outputs:** Public UI, metadata API, change feed, and metadata-only exports for research use.
- **Limitations:** Not authoritative, not current guidance, replay fidelity varies, scope intentionally constrained.

## Introduction (what + why)

- Web content in public health matters because it is used operationally (clinicians, journalists, researchers, public).
- Web guidance changes are normal, but they become hard to reconstruct after updates.
- Existing general-purpose web archives may not be optimized for:
  - discoverability via structured search,
  - consistent provenance labeling,
  - edition-aware change tracking,
  - research-ready exports and citation workflows.

## Scope and safety posture (non-negotiable)

- **What it is:** An independent archive of historical public webpages, designed for auditability and reproducibility.
- **What it is not:** A source of current guidance, a government site, or medical advice.
- **Primary audiences:** researchers, journalists, educators (secondary: clinicians/public with strong labeling).
- **Privacy posture:** no accounts, no PHI collection, minimal aggregated usage metrics only.

## System overview (architecture)

Describe at a high level (no sensitive infra details):

- **Capture pipeline:** browser-based crawling to standards-based **WARC** files.
- **Storage:** WARC files retained as the archival source of truth.
- **Indexing:** extract title/snippet/text signals into a relational database to enable fast search/browse.
- **Serving:** public API + Next.js frontend snapshot viewer; optional higher-fidelity replay.
- **Edition model:** annual “editions” anchored to a capture campaign date (e.g., Jan 01 UTC) with occasional ad-hoc captures.

## Capture methodology (how snapshots are created)

- Seed URL sets and explicit include/exclude rules per source.
- Capture outputs recorded as WARCs (HTTP responses + timestamps and metadata).
- Operational constraints:
  - scope boundaries to avoid “crawl everything” failure modes,
  - reliability > breadth,
  - respect for safe crawling practices (rate limiting, constrained seeds).

## Indexing and discovery (how users find things)

- Convert WARC records into “snapshot” rows with:
  - capture timestamp (UTC),
  - source attribution,
  - original URL,
  - stable snapshot permalink,
  - language + status code where available.
- Provide:
  - keyword search,
  - source filtering,
  - date range filtering,
  - page-grouping (“latest per page”) vs “all captures” views.

## Provenance labeling (how users avoid misuse)

- Snapshot pages and changes surfaces display:
  - capture date/time (UTC),
  - source name,
  - original URL,
  - “archival snapshot” warning / “not current guidance” callout,
  - links back to official sources.

## Change tracking (descriptive diffing)

Goal: make changes visible without interpreting meaning.

- **Edition-aware change tracking:** defaults to “changes between editions”, not “recent changes”.
- **Normalization:** extract readable text and reduce boilerplate noise.
- **Diffs:** generate human-readable comparisons and a changes feed.
- **Guardrails:** no medical interpretation; the system reports “text changed” and where.

## Research exports (metadata-only)

- Public export manifest describes formats and limits.
- Exports include:
  - snapshot metadata export (no raw HTML),
  - change event export (no diff bodies).
- Intended uses:
  - reproducible citations in papers/articles,
  - quantitative analysis of guidance drift without redistributing full copyrighted page bodies.

## Limitations and failure modes (be explicit)

- **Coverage gaps:** not all pages captured; crawling can fail; scope is limited.
- **Replay fidelity:** some JS assets or third-party embeds may not replay.
- **Temporal resolution:** annual editions mean “between-edition changes” are not real-time updates.
- **Non-authoritative:** content is archival and may be outdated or superseded.

## Ethics, governance, and corrections

- Governance pages define:
  - inclusion criteria,
  - correction workflow and response expectations,
  - takedown/opt-out process,
  - changelog discipline.

## Results (descriptive; no interpretation)

Suggested content:

- Coverage counts (sources, snapshots, pages).
- Example: a single URL’s timeline across editions.
- Example: change feed categories (new page, updated, removed/redirected).
- Usage signals (aggregated counts only; no personal identifiers).

## Discussion (what this enables)

- Reproducibility for researchers (date-stamped citations).
- Accountability and auditability for journalists and educators.
- Public-interest infrastructure value without claiming authority.

## Conclusion

- Reiterate that the contribution is infrastructure for temporal provenance and discoverability.
- State planned work at a high level:
  - more sources within scope,
  - improved diff quality,
  - versioned dataset releases (if/when adopted).

## Figures / tables (plan)

1) Architecture diagram (capture → WARC → indexing → API/UI → exports).
2) Timeline figure for one URL (captures over time).
3) Coverage table (sources + first/last capture + snapshot counts).
4) Example change comparison screenshot (with “descriptive only” labeling).

## Appendix / links (public)

- Project site: `https://healtharchive.ca`
- Governance: `https://healtharchive.ca/governance`
- Methods: `https://healtharchive.ca/methods`
- Changes + compare: `https://healtharchive.ca/changes`, `https://healtharchive.ca/compare`
- Exports: `https://healtharchive.ca/exports`
- API manifest: `https://api.healtharchive.ca/api/exports`
