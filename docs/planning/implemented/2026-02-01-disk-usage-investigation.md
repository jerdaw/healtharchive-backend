# VPS Disk Usage Investigation (Resolved 2026-02-04)

**Status:** Resolved | **Scope:** Explain and fix a large `df` vs `du` discrepancy that threatened crawl continuity.

## Outcome

This was not an ext4 accounting bug. Root cause was **annual crawl output directories ending up on the VPS root filesystem**
instead of being tiered/mounted onto the Storage Box, pushing `/` into the worker safety threshold.

## Canonical Incident Note

- `../../operations/incidents/2026-02-04-annual-crawl-output-dirs-on-root-disk.md`

## Mitigation Summary

- Pause crawls during recovery.
- Copy affected job output dirs to the Storage Box.
- Re-apply annual output tiering mounts.
- Remove local root-disk copies to restore headroom.

## Historical Context

Full investigative commands and intermediate hypotheses are preserved in git history.
