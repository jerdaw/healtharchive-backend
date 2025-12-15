# Environment & configuration matrix (frontend + backend)

This document is a **cross‑repo reference** for how the backend
(`healtharchive-backend`) and frontend (`healtharchive-frontend`) are wired
together across environments.

It is useful when:

- Setting or auditing environment variables (Vercel + backend host).
- Double‑checking that frontend hosts, backend hosts, and backend CORS settings
  line up.

For deeper operational details, see:

- `production-single-vps.md` (current production runbook)
- `hosting-and-live-server-to-dos.md` (high-level deployment checklist)
- `../operations/monitoring-and-ci-checklist.md` (uptime/monitoring guidance)
- Frontend docs: `healtharchive-frontend/docs/implementation-guide.md`
- Frontend verification: `healtharchive-frontend/docs/deployment/verification.md`

---

## 1) Environments at a glance

### What exists today

- **Single backend API**: `https://api.healtharchive.ca`
- **No separate staging backend** (by design)
- Backend **CORS allowlist** is intentionally strict:
  - `https://healtharchive.ca`
  - `https://www.healtharchive.ca`
  - `https://healtharchive.vercel.app`

Expected limitation (by design):

- Branch preview URLs like `https://healtharchive-git-...vercel.app` may fall
  back to demo mode until we explicitly allow those origins (CORS).

### Matrix

| Environment | Frontend (browser origin) | Backend API base | Notes |
| --- | --- | --- | --- |
| Local dev | `http://localhost:3000` | `http://127.0.0.1:8001` | Local dev flow. |
| Vercel project domain | `https://healtharchive.vercel.app` | `https://api.healtharchive.ca` | Allowed by CORS; useful as a stable “non-custom-domain” URL. |
| Production | `https://healtharchive.ca` / `https://www.healtharchive.ca` | `https://api.healtharchive.ca` | Primary public site. |
| Branch previews (Vercel) | `https://healtharchive-git-...vercel.app` | `https://api.healtharchive.ca` | May fall back to demo mode due to strict CORS. |

Optional future:

| Environment | Frontend (browser origin) | Backend API base | Notes |
| --- | --- | --- | --- |
| Staging API (optional) | Preview URLs or a dedicated staging frontend | `https://api-staging.healtharchive.ca` | Only if you decide you want a separate staging backend later. |

---

## 2) Backend configuration (healtharchive-backend)

All backend env vars are read by:

- `src/ha_backend/config.py`
- `src/ha_backend/api/deps.py`
- Search ranking selection is controlled by `HA_SEARCH_RANKING_VERSION` (and can be overridden per-request with `ranking=v1|v2` on `/api/search`).

### 2.1 Local development (typical)

Example shell setup (or via `.env.example` → `.env`, git-ignored):

```bash
export HEALTHARCHIVE_ENV=development
export HEALTHARCHIVE_DATABASE_URL=sqlite:///$(pwd)/.dev-healtharchive.db
export HEALTHARCHIVE_ARCHIVE_ROOT=$(pwd)/.dev-archive-root
export HEALTHARCHIVE_ADMIN_TOKEN=localdev-admin
export HEALTHARCHIVE_LOG_LEVEL=DEBUG
export HA_SEARCH_RANKING_VERSION=v2
```

### 2.2 Production (current)

On the production backend host (systemd env file / Docker env / PaaS env):

```bash
export HEALTHARCHIVE_ENV=production
export HEALTHARCHIVE_DATABASE_URL=postgresql+psycopg://healtharchive:<DB_PASSWORD>@127.0.0.1:5432/healtharchive
export HEALTHARCHIVE_ARCHIVE_ROOT=/srv/healtharchive/jobs
export HEALTHARCHIVE_ADMIN_TOKEN=<LONG_RANDOM_SECRET>
export HEALTHARCHIVE_CORS_ORIGINS=https://healtharchive.ca,https://www.healtharchive.ca,https://healtharchive.vercel.app
export HEALTHARCHIVE_LOG_LEVEL=INFO
export HA_SEARCH_RANKING_VERSION=v2
```

Notes:

- `HEALTHARCHIVE_ADMIN_TOKEN` should be a long random secret stored in a secret
  manager (e.g., Bitwarden + server env), **never committed**.
- In `production` (and `staging`), if the admin token is missing, admin/metrics
  endpoints fail closed (HTTP 500) instead of being left open.
- `HEALTHARCHIVE_CORS_ORIGINS` should be kept as narrow as possible; it controls
  which browser origins can call public API routes.

### 2.3 Optional: staging backend (future)

If you later add a separate staging backend, it should generally mirror
production except for DB/archive root and CORS origins:

```bash
export HEALTHARCHIVE_ENV=staging
export HEALTHARCHIVE_DATABASE_URL=postgresql+psycopg://healtharchive:<DB_PASSWORD>@127.0.0.1:5432/healtharchive_staging
export HEALTHARCHIVE_ARCHIVE_ROOT=/srv/healtharchive/jobs-staging
export HEALTHARCHIVE_ADMIN_TOKEN=<LONG_RANDOM_SECRET>
export HEALTHARCHIVE_CORS_ORIGINS=https://healtharchive.vercel.app
export HEALTHARCHIVE_LOG_LEVEL=INFO
```

---

## 3) Frontend configuration (healtharchive-frontend)

The frontend reads env vars at **build time**.

### 3.1 Local development

`healtharchive-frontend/.env.local` (git-ignored):

```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8001
NEXT_PUBLIC_SHOW_API_HEALTH_BANNER=true
NEXT_PUBLIC_LOG_API_HEALTH_FAILURE=true
NEXT_PUBLIC_SHOW_API_BASE_HINT=true
```

### 3.2 Vercel Production env

In Vercel → **Settings → Environment Variables → Production**:

```env
NEXT_PUBLIC_API_BASE_URL=https://api.healtharchive.ca
NEXT_PUBLIC_SHOW_API_HEALTH_BANNER=false
NEXT_PUBLIC_LOG_API_HEALTH_FAILURE=false
NEXT_PUBLIC_SHOW_API_BASE_HINT=false
```

### 3.3 Vercel Preview env

In Vercel → **Settings → Environment Variables → Preview**:

```env
NEXT_PUBLIC_API_BASE_URL=https://api.healtharchive.ca
NEXT_PUBLIC_SHOW_API_HEALTH_BANNER=true
NEXT_PUBLIC_LOG_API_HEALTH_FAILURE=true
NEXT_PUBLIC_SHOW_API_BASE_HINT=true
```

Note:

- Even with the Preview env var set, branch preview URLs may still fall back to
  demo mode unless the backend CORS allowlist includes those preview origins.

---

## 4) Security notes (secrets + CORS)

- **Never commit secrets**:
  - No real `HEALTHARCHIVE_ADMIN_TOKEN`, DB passwords, Healthchecks URLs, etc.
  - Use placeholders in docs and store real values in Bitwarden + server/Vercel
    env settings.
- **CORS is a security control**:
  - Tight allowlists reduce accidental exposure of browser-accessible APIs.
  - If you loosen CORS to include branch previews, do it deliberately and
    document the tradeoff.
