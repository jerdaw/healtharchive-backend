# Dataset Release Runbook (internal)

This release is normally hands-off (GitHub Actions). Use this checklist for verification or recovery.

## Checklist

1) Check `https://github.com/jerdaw/healtharchive-datasets/releases` for the latest tag.
2) Download all assets to one directory; run `sha256sum -c SHA256SUMS`.
3) Inspect `manifest.json` for `truncated=false` and plausible row counts.
4) Record a quarterly entry in `/srv/healtharchive/ops/adoption/` (links + aggregates only).

Notes:

- The datasets publish workflow also validates the release bundle before publishing:
  - `manifest.json` required fields + invariants (including `truncated=false`)
  - Checksums match both `manifest.json` and `SHA256SUMS`

## If a release is missing

- Manually run the **Publish dataset release** workflow in GitHub Actions.
- Confirm it creates a tag `healtharchive-dataset-YYYY-MM-DD` and uploads assets.
