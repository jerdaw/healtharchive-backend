# Dataset release integrity playbook (quarterly)

Goal: confirm a dataset release exists and its checksums verify cleanly.

Canonical reference:

- `../dataset-release-runbook.md`

## Procedure (high level)

1. Identify the latest dataset release (GitHub Releases, datasets repo).
2. Download the release assets for the quarter/date you expect.
3. Verify integrity:
   - `sha256sum -c SHA256SUMS`

## What “done” means

- `sha256sum -c SHA256SUMS` completes without errors for the latest release.

