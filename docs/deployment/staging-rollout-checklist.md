# Staging rollout checklist – backend + frontend

> Status: optional future staging reference.
>
> There is no active standalone staging backend/frontend path in the current
> direct-VPS production model. Keep this file only as a minimal placeholder
> until a real staging environment is intentionally introduced.

Documentation boundary note:

1. Shared VPS facts that are not specific to HealthArchive alone are canonical in `/home/jer/repos/platform-ops`.
2. The explicit ownership split is documented in `/home/jer/repos/platform-ops/PLAT-009-shared-vps-documentation-boundary.md`.

If you reintroduce staging later, define all of these first:

- staging API host
- staging frontend host
- exact CORS origins
- seed-data strategy
- verification route set
- rollback path

Current operator guidance:

1. use `production-single-vps.md` for the active production stack
2. use `production-rollout-checklist.md` for the active production verification flow
3. if a new staging environment is added later, rewrite this file from current reality instead of reviving the old Vercel-preview path

Once the steps above pass, you can:

- Mark the staging‑related items in `hosting-and-live-server-to-dos.md` as
  complete for the staging environment.
- Use the same patterns (with different env vars and hosts) when bringing
  production online.
