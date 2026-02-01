# Search Ranking + Snippet Quality v3 (Implemented 2026-01-18)

**Status:** Implemented | **Scope:** Improve search relevance for broad queries and snippet quality by reducing boilerplate, using Postgres FTS with lightweight heuristics.

## Outcomes

- **Ranking v3:** New scoring version with improved hub detection for broad queries like `covid`
- **Snippet extraction:** DOM pruning removes nav/header/footer/ARIA boilerplate; content-root selection prefers `<main>` or `<article>`
- **`Snapshot.is_archived` column:** Tri-state flag (NULL/true/false) computed at indexing time from title + banner signals
- **FTS vector input:** ~4KB of cleaned main content (up from ~2KB) improves recall
- **Golden query evaluation:** Repeatable capture + diff workflow with artifacts stored on VPS

## Canonical Docs Updated

- Search quality: [operations/search-quality.md](../../operations/search-quality.md)
- Golden queries: [operations/search-golden-queries.md](../../operations/search-golden-queries.md)
- Search rollout: [deployment/search-rollout.md](../../deployment/search-rollout.md) (v2 → v3)
- Architecture: [architecture.md](../../architecture.md) (search section)

## Key Design Decisions

- **No new search infrastructure:** Postgres FTS + heuristics; no Elasticsearch/Meilisearch
- **Generic-first snippet heuristics:** Bilingual boilerplate phrase list; Canada.ca-specific rules only if needed
- **Reversible rollout:** `HA_SEARCH_RANKING_VERSION=v3` env var; rollback is single-var flip
- **Archived detection in DB:** Cheaper and more stable than query-time snippet heuristics

## Scripts Updated

- `scripts/search-eval-capture.sh` — Supports `--ranking v3`
- `scripts/search-eval-run.sh` — v2 vs v3 comparisons

## Backfill Commands

```bash
# Refresh snippets for recent jobs
ha-backend refresh-snapshot-metadata --job-id <ID>

# Backfill search vectors (off-peak)
ha-backend backfill-search-vector --force
```

## Historical Context

7-phase sequential implementation (680+ lines) with baseline captures, snippet failure taxonomy, v3 scoring spec, evaluation loop, and rollout procedure. Preserved in git history.
