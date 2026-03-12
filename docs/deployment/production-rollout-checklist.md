# Production rollout checklist – backend + frontend

This is the active high-level production verification checklist for the current
HealthArchive direct-VPS deployment.

Current production reality:

- frontend canonical host: `https://healtharchive.ca`
- frontend alias: `https://www.healtharchive.ca` -> apex redirect
- backend API: `https://api.healtharchive.ca`
- replay host (optional): `https://replay.healtharchive.ca`
- public ingress owner: host Caddy on the Hetzner VPS

Use this file as the quick production checklist. Use
[`production-single-vps.md`](production-single-vps.md) as the full rebuild and
deployment runbook.

Documentation boundary note:

1. This checklist is canonical for the active HealthArchive production verification flow.
2. Shared VPS facts that are not specific to HealthArchive alone are canonical in `/home/jer/repos/platform-ops`.
3. The explicit ownership split is documented in `/home/jer/repos/platform-ops/PLAT-009-shared-vps-documentation-boundary.md`.

## 1. Preconditions

- [ ] the backend VPS env file is current (`/etc/healtharchive/backend.env`)
- [ ] code from `main` is deployed at the intended revision
- [ ] `make prepush` or equivalent repo checks passed before deployment
- [ ] any schema changes have been migrated and verified
- [ ] `ha-backend seed-sources` has been run where needed

References:

- `production-single-vps.md`
- `environments-and-configuration.md`
- `hosting-and-live-server-to-dos.md`

## 2. Backend runtime checks

On the VPS:

```bash
curl -i http://127.0.0.1:8001/api/health
sudo systemctl status healtharchive-api --no-pager --lines=20
sudo systemctl status healtharchive-worker --no-pager --lines=20
```

- [ ] local API health returns `200`
- [ ] API service is active
- [ ] worker service is active

If the ranking or pages pipeline changed, also run:

```bash
sudo systemd-run --wait --pipe \
  --property=EnvironmentFile=/etc/healtharchive/backend.env \
  /opt/healtharchive-backend/.venv/bin/ha-backend recompute-page-signals

sudo systemd-run --wait --pipe \
  --property=EnvironmentFile=/etc/healtharchive/backend.env \
  /opt/healtharchive-backend/.venv/bin/ha-backend rebuild-pages --truncate
```

## 3. Public API verification

From any machine with internet access:

```bash
curl -i https://api.healtharchive.ca/api/health
curl -i -H "Origin: https://healtharchive.ca" https://api.healtharchive.ca/api/health
```

- [ ] API returns `200`
- [ ] JSON body contains `"status":"ok"`
- [ ] CORS allows `https://healtharchive.ca`
- [ ] `Vary: Origin` is present

## 4. Frontend verification

Run:

```bash
curl -I https://healtharchive.ca
curl -I https://www.healtharchive.ca
curl -I https://healtharchive.ca/archive
curl -I https://healtharchive.ca/snapshot/1
```

- [ ] apex responds normally
- [ ] `www` redirects to the apex
- [ ] `/archive` is reachable
- [ ] snapshot route is reachable

In a browser, also verify:

- [ ] `https://healtharchive.ca/archive` shows live archive data
- [ ] `https://healtharchive.ca/archive/browse-by-source` shows real source counts
- [ ] `https://healtharchive.ca/snapshot/<id>` embeds the archived content successfully
- [ ] raw snapshot links resolve under `https://api.healtharchive.ca/api/snapshots/raw/<id>`

## 5. Monitoring and automation checks

- [ ] external uptime monitors are green for:
  - `https://api.healtharchive.ca/api/health`
  - `https://healtharchive.ca/archive`
  - `https://replay.healtharchive.ca/` if replay is in active use
- [ ] systemd timer pings remain healthy if Healthchecks is configured
- [ ] Prometheus/Grafana/Alertmanager remain healthy if observability is enabled

Recommended checks:

```bash
./scripts/smoke-external-monitors.sh
./scripts/verify_public_surface.py --api-base https://api.healtharchive.ca --frontend-base https://healtharchive.ca
```

## 6. Rollback trigger

Rollback or stop rollout if any of these are true:

- API health fails
- frontend apex is unreachable
- `www` no longer redirects correctly
- snapshot replay or raw snapshot responses fail for known-good records
- worker fails to stay active after restart

Use `production-single-vps.md` for the full rollback path.
  - Verify backend `main` protection still matches the solo-dev ruleset profile in
    `../operations/monitoring-and-ci-checklist.md` §3.2:
    - required check: `Backend CI / test`
    - keep `Backend CI / e2e-smoke` and `Backend CI (Full) / test-full` non-required
    - keep `Restrict deletions` and `Block force pushes` enabled

With this in place, `main` deploys cleanly to production, and you have health
and metrics coverage for both the API and the frontend.
