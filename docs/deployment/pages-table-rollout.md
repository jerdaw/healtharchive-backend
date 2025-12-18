# Pages table rollout (browse performance + capture counts)

The backend can optionally maintain a `pages` table that materializes a
per‑source “page” concept (grouped by `normalized_url_group`) from the raw
`snapshots` table.

Important: this is **metadata only**. It does **not** modify WARCs, does **not**
delete snapshots, and does **not** affect replay fidelity.

## What it improves

- **Browse performance** for `GET /api/search?view=pages` when there is **no**
  search query (and no date range). This avoids expensive window functions over
  the entire `snapshots` table.
- Adds `pageSnapshotsCount` to page-browse results so the frontend can show
  “Captures N”.

Keyword searches (`q=...`) and date-range filters still run directly against
`snapshots` to keep correctness predictable.

## Rollout steps (production)

1) Apply migrations:

```bash
./.venv/bin/alembic upgrade head
```

2) Backfill the table once:

```bash
./.venv/bin/ha-backend rebuild-pages --truncate
```

Notes:

- For large datasets this can take a while; run it in `tmux` or off-peak.
- The worker will keep the table updated for newly indexed jobs (incremental
  rebuilds happen after indexing).

## Verification

1) Confirm pages browse includes capture counts:

```bash
curl -s "https://api.healtharchive.ca/api/search?view=pages&pageSize=1" | python -m json.tool | head
```

You should see `pageSnapshotsCount` as an integer (not `null`) on results.

2) Confirm metrics (admin token required):

```bash
curl -s https://api.healtharchive.ca/metrics \
  -H "Authorization: Bearer $HEALTHARCHIVE_ADMIN_TOKEN" \
  | grep -E "healtharchive_pages_(table_present|total|fastpath_enabled)|healtharchive_search_mode_total\\{mode=\\\"pages_fastpath\\\"\\}"
```

## Rollback / safety valve

If anything looks suspicious in production (for example: browse ordering or
unexpected results), you can disable the fast path without touching data:

1) Set `HA_PAGES_FASTPATH=0` in `/etc/healtharchive/backend.env`
2) Restart only the API process:

```bash
sudo systemctl restart healtharchive-api
```

This forces `view=pages` browsing to fall back to snapshot-based grouping.
