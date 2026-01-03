# Golden queries (search relevance)

This file is a **living checklist** of “golden queries” used to evaluate whether
search ranking changes improve relevance.

It complements `search-quality.md`:

- `search-quality.md` explains *how* to run an evaluation.
- This file defines *what* we check (queries + expected outcomes).

## 1) How to use this file

For each query below:

1. Run the API captures (prefer `view=pages` for user-facing relevance).
2. Compare the **top ~10** results against the expectations.
3. Update expectations when the archive’s coverage changes (new sources/pages).

Guiding principle:

- Broad queries should surface **hub/overview pages** near the top.
- Specific queries should surface the **most directly matching documents**.

## 2) Query set (curated, no query logs required)

We do not have query logs yet, so this list is a **curated approximation** of:

- Broad hub intent (1–2 terms).
- Common refinements (testing, vaccines, isolation).
- Specific named entities (NACI, antivirals).
- A few non-COVID queries (to avoid overfitting).
- A small French “smoke test” set for bilingual content.

### 2.1 Broad “head” queries (hub intent)

- `covid`
- `influenza`
- `mpox`
- `measles`
- `rsv`
- `food recall`
- `travel advisory`
- `mental health`
- `air quality`
- `wildfire smoke`
- `immunization`
- `vaccines`

### 2.2 Medium queries (mixed intent)

- `covid vaccine`
- `covid booster`
- `mask`
- `mask guidance`
- `rapid testing`
- `testing`
- `wastewater`
- `isolation`
- `quarantine`
- `symptoms`
- `treatment`
- `prevention risks`

### 2.3 Specific queries (precision intent)

- `long covid`
- `post covid condition`
- `naci`
- `naci booster`
- `myocarditis pericarditis`
- `omicron ventilation`
- `ventilation filtration`
- `paxlovid`
- `nirmatrelvir`
- `remdesivir`
- `health infobase`

### 2.4 Non-COVID queries (overfitting guardrail)

- `opioid overdose`
- `naloxone`
- `vaping`
- `cannabis`
- `antimicrobial resistance`
- `water advisory`

### 2.5 French smoke tests (bilingual content; no stemming)

- `grippe`
- `variole simienne`
- `vaccin covid`
- `sante mentale`

## 3) Expectations (fill these in over time)

Use **normalized URL groups** where possible (what `view=pages` groups on). For
each query, keep:

- “Expected” pages: should appear in top 10 (ideally top 3–5 for broad queries).
- “Anti-results”: should *not* appear in top 20 for broad queries.

### 3.1 `covid`

**Expected (top 10):**

- `https://www.canada.ca/en/public-health/services/diseases/coronavirus-disease-covid-19.html`
- `https://www.canada.ca/en/public-health/services/diseases/2019-novel-coronavirus-infection.html`

**Nice-to-have (top 20):**

- `https://www.canada.ca/en/public-health/services/diseases/2019-novel-coronavirus-infection/prevention-risks.html`
- `https://travel.gc.ca/travel-covid`

**Anti-results (avoid in top 20 for broad `covid` unless explicitly requested):**

- Titles beginning with `Archived` (e.g., `Archived - ...`, `Archived 50: ...`)
- Narrow deep pages that win solely by repeating “COVID” many times in title/snippet

### 3.2 `long covid`

**Expected (top 10):**

- `https://www.canada.ca/en/public-health/services/diseases/2019-novel-coronavirus-infection/symptoms/post-covid-19-condition.html`
- `https://www.canada.ca/en/public-health/services/diseases/2019-novel-coronavirus-infection/health-professionals/post-covid-19-condition.html`

**Anti-results:**

- Broad COVID hubs outranking long-COVID pages (unless query is just `covid`)

### 3.3 Other queries

Fill these in as coverage grows and you learn what “good” looks like:

- `influenza`: expected hub pages, PHAC/HC overview pages
- `mpox`: expected public health hub pages
- `food recall`: expected recall hub pages (e.g., CFIA)
- `travel advisory`: expected Travel.gc.ca hub pages
- `mental health`: expected PHAC/HC hub pages

## 4) Capture commands (recommended)

Prefer capturing API responses (fast + deterministic).

Production examples:

```bash
curl -s "https://api.healtharchive.ca/api/search?q=covid&page=1&pageSize=20&sort=relevance&view=pages" | python3 -m json.tool
curl -s "https://api.healtharchive.ca/api/search?q=long%20covid&page=1&pageSize=20&sort=relevance&view=pages" | python3 -m json.tool
```

To force a specific ranking version (useful for comparisons):

```bash
curl -s "https://api.healtharchive.ca/api/search?q=covid&page=1&pageSize=20&sort=relevance&view=pages&ranking=v2" | python3 -m json.tool
```

Local examples (backend on `127.0.0.1:8001`):

```bash
curl -s "http://127.0.0.1:8001/api/search?q=covid&page=1&pageSize=20&sort=relevance&view=pages" | python3 -m json.tool
```

For a repeatable “before/after” capture directory, use the script:

```bash
./scripts/search-eval-capture.sh --base-url https://api.healtharchive.ca --out-dir /tmp/ha-search-eval
```

To capture using ranking v2 explicitly:

```bash
./scripts/search-eval-capture.sh --ranking v2 --base-url https://api.healtharchive.ca --out-dir /tmp/ha-search-eval
```

To additionally generate corpus-derived queries from the configured database and
merge them with the curated list:

```bash
./scripts/search-eval-capture.sh --generate-from-db --out-dir /tmp/ha-search-eval
```

Notes:

- Keep captures out of git unless you explicitly want them committed.
- Prefer `view=pages` for ranking evaluation; use `view=snapshots` to debug capture-level issues.
