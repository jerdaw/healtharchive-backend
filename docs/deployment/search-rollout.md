# Search ranking rollout (v2 default)

This is the recommended rollout procedure for enabling the blended search ranking (**v2**) in production.

Decision: **use v2 by default** via `HA_SEARCH_RANKING_VERSION=v2`.

Rationale (given current project goals/resources):
- We’re intentionally staying on Postgres FTS + lightweight heuristics (no separate search service).
- v2 materially improves broad-query “hub” discovery using signals we already compute (`page_signals`, `snapshot_outlinks`).
- Rollback is instant and low-risk (flip one env var + restart API).

## 0) Preconditions

- You have already deployed the authority schema (tables `snapshot_outlinks` and `page_signals`) and populated outlinks for your jobs.
  - If `snapshot_outlinks` is empty, v2 won’t get the link-graph benefits.
- You have the admin token available (for `/api/admin/search-debug` verification).

## 1) Recommended production rollout steps (single VPS)

On the VPS (as `haadmin`) in the backend repo checkout (e.g. `/opt/healtharchive-backend`):

1) Pull the new backend revision.
2) Apply migrations:
   - `./.venv/bin/alembic upgrade head`
3) Recompute link signals (populates `inlink_count`, `outlink_count`, and `pagerank` when present):
   - `./.venv/bin/ha-backend recompute-page-signals`
4) Enable v2 by default:
   - Edit `/etc/healtharchive/backend.env` and set `HA_SEARCH_RANKING_VERSION=v2`
5) Restart only the API process (worker does not need the ranking env var):
   - `sudo systemctl restart healtharchive-api`

Notes:
- `ha-backend recompute-page-signals` can take a while on large graphs; run it in `tmux` and consider off-peak hours.
- If you have not yet backfilled outlinks for existing WARCs, do that first (per job):
  - `./.venv/bin/ha-backend backfill-outlinks --job-id <JOB_ID> --update-signals`
  - Then run `./.venv/bin/ha-backend recompute-page-signals` once after the backfills finish.

## 2) Verification checklist (production)

1) Health:
   - `curl -s https://api.healtharchive.ca/api/health | python -m json.tool`
2) Search v2 is active by default:
   - `curl -s "https://api.healtharchive.ca/api/search?q=covid&view=pages&sort=relevance&pageSize=20" | python -m json.tool | head`
3) Compare explicitly:
   - `curl -s "https://api.healtharchive.ca/api/search?q=covid&view=pages&sort=relevance&pageSize=20&ranking=v1" | python -m json.tool | head`
   - `curl -s "https://api.healtharchive.ca/api/search?q=covid&view=pages&sort=relevance&pageSize=20&ranking=v2" | python -m json.tool | head`
4) Debug a ranking decision (admin token required):
   - `curl -s "https://api.healtharchive.ca/api/admin/search-debug?q=covid&view=pages&sort=relevance&ranking=v2&pageSize=10" -H "X-Admin-Token: $HEALTHARCHIVE_ADMIN_TOKEN" | python -m json.tool`

## 3) Capture + diff (recommended “smoke eval”)

From any machine (your laptop is fine):

1) Capture:
   - `./scripts/search-eval-capture.sh --base-url https://api.healtharchive.ca --out-dir /tmp/ha-search-eval --page-size 20 --ranking v1`
   - `./scripts/search-eval-capture.sh --base-url https://api.healtharchive.ca --out-dir /tmp/ha-search-eval --page-size 20 --ranking v2`
2) Diff:
   - `python ./scripts/search-eval-diff.py --a /tmp/ha-search-eval/<TS_V1> --b /tmp/ha-search-eval/<TS_V2> --top 20`

## 4) Rollback plan (fast)

If something looks off in production:

1) Set `HA_SEARCH_RANKING_VERSION=v1` in `/etc/healtharchive/backend.env`
2) `sudo systemctl restart healtharchive-api`

This reverts default behavior immediately without touching data/migrations. You can still test v2 per-request with `&ranking=v2` while investigating.

