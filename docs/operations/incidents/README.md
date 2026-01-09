# Incident notes (internal)

This folder contains **incident notes / lightweight postmortems** for production and operations issues.

Goals:

- Capture what happened (timeline + impact) while it’s fresh.
- Record the **root cause** and **recovery steps** (so we can repeat them safely).
- Track “still to do” work that reduces repeat incidents (docs, guardrails, automation).

Related:

- Operator recovery steps: `../playbooks/incident-response.md`
- Ops playbooks index: `../playbooks/README.md`
- Severity rubric: `severity.md`

---

## What goes here

Use an incident note when any of the following are true:

- Public site/API degraded or down.
- Crawl/indexing is stuck, repeatedly failing, or risking data integrity.
- Storage/mount issues (e.g. Errno 107) or “hot path” problems.
- You had to do manual intervention beyond routine operations.

Do not use incident notes for planned maintenance; record that in the changelog (and/or a runbook update) instead.

---

## Naming

One file per incident:

- `YYYY-MM-DD-<short-slug>.md` (use **UTC** date of incident start).
- If multiple incidents share a date, add a suffix: `...-a`, `...-b`.

Example:

- `2026-01-09-annual-crawl-phac-output-dir-permission-denied.md`

---

## How to write one

1) Copy the template: `incident-template.md`
2) Fill the top metadata and a short summary immediately.
3) Add a timeline (UTC) as you work.
4) After recovery, fill root cause + follow-ups.
5) Link any follow-up playbooks/runbooks/roadmaps you touched.
6) If this incident changes user expectations (outage/degradation, integrity risk, security posture, policy change), add a **public-safe** note in `/changelog` and/or `/status` (no sensitive details; changelog process: `healtharchive-frontend/docs/changelog-process.md`).

The template includes an **Action items (TODOs)** section; use checkboxes so it’s obvious what work remains.

If the incident requires engineering work (automation, new scripts, behavior changes), capture it as a follow-up and create a focused implementation plan under `docs/roadmaps/` (then link it from the incident note).

### What not to include

- Secrets (tokens, passwords, Healthchecks URLs).
- Private emails or non-public IPs/hostnames.
- Full logs. Prefer:
  - the **exact** log path(s),
  - the most relevant ~20–50 lines, and
  - one or two `vps-*.sh` snapshots.

---

## Style

- Keep it blameless: focus on systems, invariants, and guardrails (not individuals).
- Prefer concrete facts over speculation; if something is unknown, label it as such.
- Record commands that changed state (DB writes, mounts, restarts) and what they affected.
