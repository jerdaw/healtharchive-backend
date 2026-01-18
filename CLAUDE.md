# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Reference

This is the **HealthArchive.ca backend** - a Python service that orchestrates web archiving of Canadian health government sources (Health Canada, PHAC), indexes WARC files into a relational database, and exposes HTTP APIs for search and retrieval.

**Canonical AI instructions**: See `AGENTS.md` for detailed project conventions, data model, safety rails, and guidelines. This file supplements that with quick-reference commands.

## Build & Development Commands

```bash
make venv              # Create virtualenv + install dependencies
make ci                # Fast gate: format-check + lint + typecheck + test
make check-full        # Stricter: + pre-commit + security + audit + docs-check
make docs-serve        # Serve docs locally at http://localhost:8000
```

## Running a Single Test

```bash
pytest tests/test_api.py::test_health_endpoint -v     # Single test
pytest tests/test_api.py -v                           # Single file
pytest -k "search" -v                                 # Pattern match
```

## Local Development

```bash
# Setup
source .venv/bin/activate
cp .env.example .env && source .env
alembic upgrade head

# Run API server
uvicorn ha_backend.api:app --reload --port 8001

# Run worker (polls for jobs)
ha-backend start-worker --poll-interval 30
```

## CLI Quick Reference

```bash
ha-backend seed-sources                    # Initialize source records
ha-backend create-job --source hc          # Queue a Health Canada crawl
ha-backend run-db-job --id 42              # Execute a specific job
ha-backend index-job --id 42               # Index WARCs into snapshots
ha-backend compute-changes                 # Generate change events
ha-backend list-jobs / show-job --id 42    # Inspect jobs
```

## Architecture Overview

```
src/
├── ha_backend/              # Core backend package
│   ├── api/                 # FastAPI routes (public + admin)
│   ├── indexing/            # WARC parsing, text extraction
│   ├── worker/              # Job processor loop
│   ├── models.py            # ORM: Source, ArchiveJob, Snapshot
│   ├── jobs.py              # Job execution + archive_tool integration
│   └── job_registry.py      # Per-source job config templates
└── archive_tool/            # Internal crawler subpackage (zimit/Docker)
```

**Data flow**: Job creation → Crawl (archive_tool subprocess) → WARC indexing → Snapshot rows → API serving

**Key models**: `Source` (content origin), `ArchiveJob` (crawl lifecycle), `Snapshot` (captured page)

## Key Documentation

- `docs/architecture.md` - Deep implementation walkthrough
- `docs/development/live-testing.md` - Step-by-step local flows
- `docs/deployment/production-single-vps.md` - Production runbook
- `src/archive_tool/docs/documentation.md` - Crawler internals
