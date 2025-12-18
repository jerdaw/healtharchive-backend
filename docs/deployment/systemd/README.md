# Systemd unit templates (single VPS)

These files are **templates** meant to be copied onto the production VPS under
`/etc/systemd/system/`.

They implement:

- Phase 5: annual scheduling timer (Jan 01 UTC)
- Phase 6: worker priority lowering during campaign (always-on, low-risk)

Assumptions (adjust paths/user if your VPS differs):

- Repo is deployed at: `/opt/healtharchive-backend`
- Venv exists at: `/opt/healtharchive-backend/.venv`
- Backend env file: `/etc/healtharchive/backend.env`
- Backend system user: `haadmin`

---

## Files

- `healtharchive-schedule-annual.service`
  - **Apply mode**: enqueues annual jobs (`--apply`) for the current UTC year.
  - Gated by `ConditionPathExists=/etc/healtharchive/automation-enabled`.
  - `RefuseManualStart=yes` to prevent accidental `systemctl start` while the
    worker is running.
- `healtharchive-schedule-annual.timer`
  - Runs at `*-01-01 00:05:00 UTC`
  - `Persistent=true` (runs on next boot if missed)
- `healtharchive-schedule-annual-dry-run.service`
  - Safe validation service (no DB writes).
- `healtharchive-worker.service.override.conf`
  - Drop-in that lowers worker CPU/IO priority to keep the API responsive.

---

## Install / update on the VPS

Copy unit files:

```bash
sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-schedule-annual.service \
  /etc/systemd/system/healtharchive-schedule-annual.service

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-schedule-annual.timer \
  /etc/systemd/system/healtharchive-schedule-annual.timer

sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-schedule-annual-dry-run.service \
  /etc/systemd/system/healtharchive-schedule-annual-dry-run.service
```

Install the worker priority drop-in:

```bash
sudo install -d -m 0755 -o root -g root /etc/systemd/system/healtharchive-worker.service.d
sudo install -m 0644 -o root -g root \
  /opt/healtharchive-backend/docs/deployment/systemd/healtharchive-worker.service.override.conf \
  /etc/systemd/system/healtharchive-worker.service.d/override.conf
```

Reload systemd:

```bash
sudo systemctl daemon-reload
```

Restart worker to pick up priority changes:

```bash
sudo systemctl restart healtharchive-worker
```

Verify the priority values:

```bash
systemctl show healtharchive-worker -p Nice -p IOSchedulingClass -p IOSchedulingPriority
```

---

## Validate the annual scheduler (safe)

This dry-run service exercises DB connectivity + scheduler output without
creating jobs:

```bash
sudo systemctl start healtharchive-schedule-annual-dry-run.service
sudo journalctl -u healtharchive-schedule-annual-dry-run.service -n 200 --no-pager
```

Do **not** run `healtharchive-schedule-annual.service` manually in production;
it enqueues jobs and the worker may start crawling immediately.

---

## Enable automation (Jan 01)

Create the automation sentinel file:

```bash
sudo install -m 0644 -o root -g root /dev/null /etc/healtharchive/automation-enabled
```

Enable the timer:

```bash
sudo systemctl enable --now healtharchive-schedule-annual.timer
systemctl list-timers | rg healtharchive-schedule-annual || systemctl list-timers | grep healtharchive-schedule-annual
```

Note: do not `enable` the `.service` units directly; only the `.timer` should be
enabled.

---

## Rollback / disable quickly

- Disable timer immediately:

  ```bash
  sudo systemctl disable --now healtharchive-schedule-annual.timer
  ```

- Disable all scheduling automation immediately:

  ```bash
  sudo rm -f /etc/healtharchive/automation-enabled
  ```

- Remove the worker priority override:

  ```bash
  sudo rm -f /etc/systemd/system/healtharchive-worker.service.d/override.conf
  sudo systemctl daemon-reload
  sudo systemctl restart healtharchive-worker
  ```
