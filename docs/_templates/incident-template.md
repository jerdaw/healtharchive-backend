# Incident: <short title> (YYYY-MM-DD)

Status: draft | closed

## Metadata

- Date (UTC): YYYY-MM-DD
- Severity (see `severity.md`): sev0 | sev1 | sev2 | sev3
- Environment: production | staging | dev
- Primary area: crawl | indexing | storage | api | replay | search | infra
- Owner: <name/handle>
- Start (UTC): YYYY-MM-DDTHH:MM:SSZ
- End (UTC): YYYY-MM-DDTHH:MM:SSZ (or ongoing)

---

## Summary

What happened in 2–5 sentences, written for someone who wasn’t online during the incident.

## Impact

- User-facing impact:
- Internal impact (ops burden, automation failures, etc):
- Data impact:
  - Data loss: yes/no/unknown
  - Data integrity risk: yes/no/unknown
  - Recovery completeness: complete/partial/unknown
- Duration:

## Detection

- How was it detected (alert, operator check, user report)?
- What signals were most useful (commands/metrics/logs)?

## Decision log (optional but recommended for sev0/sev1)

Record key decisions and why they were made (especially if they trade off data integrity vs speed).

- YYYY-MM-DDTHH:MM:SSZ — Decision: <what> (why: <reason>, risks: <known risks>)

## Timeline (UTC)

Keep this as a chronological log. Prefer timestamps.

- YYYY-MM-DDTHH:MM:SSZ — <event>
- YYYY-MM-DDTHH:MM:SSZ — <event>

## Root cause

- Immediate trigger:
- Underlying cause(s):

## Contributing factors

- What made this worse or harder to debug?

## Resolution / Recovery

Describe the recovery steps taken, in the order performed, with commands if helpful.

## Post-incident verification

What you did to confirm we’re actually healthy (and not just “running”).

- Public surface checks:
- Worker/job health checks:
- Storage/mount checks (if relevant):
- Integrity checks (if relevant):

## Public communication (optional; do this when it changes user expectations)

Keep this public-safe (no sensitive incident details).

- Public status update (where/when):
- Changelog entry (date/link):
- Public summary (2–5 sentences):

## Open questions (still unknown)

- <question>
- <question>

## Action items (TODOs)

Make these specific, small, and verifiable. Link to issues/PRs/roadmaps if they exist.

- [ ] <action> (owner=, priority=, due=)
- [ ] <action> (owner=, priority=, due=)

## Automation opportunities

- What can be automated safely?
- What should stay manual (risk/false positives)?

## References / Artifacts

- `./scripts/vps-crawl-status.sh` snapshot(s):
- Relevant log path(s):
- Dashboard link(s) / metric names:
- Related playbooks/runbooks:
