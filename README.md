# HealthArchive.ca – Backend

This repository contains the backend services and archiving pipeline for
[HealthArchive.ca](https://healtharchive.ca).

It currently includes:

- A vendored copy of `archive_tool`, a wrapper around `zimit`/Docker used to
  crawl and archive web content.
- A basic configuration module.
- A small CLI for environment checks.

## Layout

```text
healtharchive-backend/
  pyproject.toml
  requirements.txt
  .gitignore
  src/
    ha_backend/
      __init__.py
      config.py
      cli.py
    archive_tool/
      ... (vendored from zimit-scraper-aio)
  scripts/
    run_archive.py        # legacy-style archive_tool entrypoint

