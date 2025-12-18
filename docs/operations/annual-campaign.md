# Annual Capture Campaign (Jan 01 UTC) — Scope, Sources, Seeds

Status: **approved (v1 scope)** — single VPS, production-only.

This document defines the **canonical scope** of HealthArchive’s annual crawl
campaign:

-   Runs **once per year** on **Jan 01 (UTC)**.
-   Uses **no page/depth limits** (completeness and accuracy are the priority).
-   Targets a **small, stable set of sources** to keep operations reliable on a
    single VPS.
-   Optimizes for getting the annual capture **indexed and searchable** as soon as
    each crawl completes (replay + preview automation is explicitly secondary).

This doc is intentionally focused on “what we crawl” and “where we start”.
Implementation details (scheduler, timers, reconciler, monitoring) live in:

-   `automation-implementation-plan.md`

---

## 1) Goals and non-goals

### Goals

-   **Annual snapshot semantics:** each source gets one “annual edition” per year,
    labeled as Jan 01 (UTC) for that year.
-   **Completeness and accuracy:** do not artificially cap depth/pages. Prefer
    broad coverage of each source, even if crawls take days.
-   **Search-first readiness:** once a crawl finishes, indexing should run next so
    results become searchable as quickly as possible on production hardware.
-   **Stable scope:** only include sources we can realistically crawl and operate
    with minimal ongoing tweaking.

### Non-goals (for v1)

-   Adding many new sources quickly (scope explosion).
-   Achieving a literally simultaneous capture moment across all sources (single
    VPS + limited concurrency makes this unrealistic).
-   Automating cleanup/retention that could delete WARCs (explicitly deferred).
-   Building a separate staging environment.

---

## 2) Canonical sources (v1)

For the initial annual campaign, we intentionally crawl only three sources:

1. **Health Canada** (`hc`)
2. **Public Health Agency of Canada** (`phac`)
3. **Canadian Institutes of Health Research** (`cihr`)

Rationale:

-   These are core, high-value federal public health sources.
-   They keep the campaign small enough to remain operable on a single VPS.
-   They map cleanly to existing backend concepts (`Source`, `ArchiveJob`).

---

## 3) Canonical seeds (entry URLs)

Seeds are the “entry points” from which the crawler discovers pages.

Important notes:

-   Seeds must be **stable** and **canonical** (avoid ephemeral campaign pages).
-   For bilingual sites, include **both English and French entry points** so
    coverage does not depend on cross-link discovery.
-   Seeds should be chosen to represent “the main hub” of the source.

### 3.1 Source table

| Code   | Source                         | Primary host(s)   | English seed                                  | French seed                                    |
| ------ | ------------------------------ | ----------------- | --------------------------------------------- | ---------------------------------------------- |
| `hc`   | Health Canada                  | `www.canada.ca`   | `https://www.canada.ca/en/health-canada.html` | `https://www.canada.ca/fr/sante-canada.html`   |
| `phac` | Public Health Agency of Canada | `www.canada.ca`   | `https://www.canada.ca/en/public-health.html` | `https://www.canada.ca/fr/sante-publique.html` |
| `cihr` | CIHR                           | `cihr-irsc.gc.ca` | `https://cihr-irsc.gc.ca/e/193.html`          | `https://cihr-irsc.gc.ca/f/193.html`           |

### 3.2 Scope boundary notes (important)

These are policy decisions to prevent crawls from ballooning unexpectedly while
still preserving completeness within each source:

-   **Primary scope boundary (required):** each source has an explicit, mechanical
    “in-scope URL rule”.
-   **Cross-domain assets:** pages will reference external assets (fonts, JS,
    images). Capturing all third-party assets is not required for search indexing.
    If replay fidelity requires specific additional domains later, add them
    explicitly and sparingly (do not allow arbitrary internet expansion).
-   **Canada.ca shared host:** `hc` and `phac` both live on `www.canada.ca`.
    We must scope by **host + path allowlist** (not “all of `www.canada.ca`”).

### 3.3 In-scope URL rules (mechanical, v1)

These rules define “what counts as Health Canada / PHAC” on a shared host.

#### Health Canada (`hc`) — `www.canada.ca`

In scope:

-   Exactly:
    -   `https://www.canada.ca/en/health-canada.html`
    -   `https://www.canada.ca/fr/sante-canada.html`
-   Any URL under these path prefixes:
    -   `https://www.canada.ca/en/health-canada/`
    -   `https://www.canada.ca/fr/sante-canada/`
-   Any URL under these asset path prefixes (captured only when referenced by in-scope pages):
    -   `https://www.canada.ca/etc/designs/canada/wet-boew/`
    -   `https://www.canada.ca/content/dam/canada/sitemenu/`
    -   `https://www.canada.ca/content/dam/themes/health/`
    -   `https://www.canada.ca/content/dam/hc-sc/`

Out of scope (examples):

-   `https://www.canada.ca/en/services/`
-   `https://www.canada.ca/en/government/`
-   Any other `https://www.canada.ca/<lang>/...` that is not the hub page or under
    the allowed prefixes above.

#### PHAC (`phac`) — `www.canada.ca`

In scope:

-   Exactly:
    -   `https://www.canada.ca/en/public-health.html`
    -   `https://www.canada.ca/fr/sante-publique.html`
-   Any URL under these path prefixes:
    -   `https://www.canada.ca/en/public-health/`
    -   `https://www.canada.ca/fr/sante-publique/`
-   Any URL under these asset path prefixes (captured only when referenced by in-scope pages):
    -   `https://www.canada.ca/etc/designs/canada/wet-boew/`
    -   `https://www.canada.ca/content/dam/canada/sitemenu/`
    -   `https://www.canada.ca/content/dam/themes/health/`
    -   `https://www.canada.ca/content/dam/phac-aspc/`

Out of scope (examples):

-   `https://www.canada.ca/en/services/`
-   `https://www.canada.ca/en/government/`
-   Any other `https://www.canada.ca/<lang>/...` that is not the hub page or under
    the allowed prefixes above.

#### CIHR (`cihr`) — `cihr-irsc.gc.ca`

In scope:

-   Any URL on host `cihr-irsc.gc.ca` (all paths), starting from the EN+FR seeds
    above.

Out of scope:

-   Any other host.

This is a “completeness” project, not an “infinite crawl” project: completeness
means “complete within the intended source boundaries.”

---

## 4) Campaign ordering (single VPS reality)

With a single production VPS and limited parallelism, crawls and indexing will
not complete simultaneously across sources.

We still want the annual snapshot to feel like “one moment in time”, so we
should order annual jobs to minimize the spread between the first and last job
to finish indexing.

Opinionated default ordering for v1:

1. `hc` (expected to be largest / slowest)
2. `phac`
3. `cihr` (expected to be smallest / fastest)

Rationale:

-   Running the slowest job first reduces “finish-time spread” across the set.
-   A fixed ordering makes operations reproducible year over year.

After the first annual campaign completes, revisit ordering based on real job
durations and storage growth.

---

## 5) What “done” means (per source)

For each source’s annual job:

1. Job reaches `status=indexed` (searchable).
2. `/api/search` returns results for that source and year as expected.

Replay and previews are “eventually consistent” follow-ups and are not part of
the “search is ready” definition for the annual campaign.
