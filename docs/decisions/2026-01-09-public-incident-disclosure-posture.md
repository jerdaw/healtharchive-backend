# Decision: Public incident disclosure posture (Option B for now) (2026-01-09)

Status: accepted

## Context

- HealthArchive is early in its operational lifecycle and will likely see a higher rate of sev0/sev1 incidents while reliability work is still being built out.
- We want to maintain transparency without creating constant public “incident noise” that trains users to ignore updates.
- We already capture internal, public-safe incident notes for operational learning and repeatability under `docs/operations/incidents/`.

## Decision

- We will use **Option B** by default:
  - publish a **public-safe** note (in `/changelog` and/or `/status`) **only when** an incident changes user expectations:
    - user-visible outage or major degradation,
    - credible integrity risk,
    - security posture change,
    - public policy/governance change.
- We will still write internal incident notes when appropriate (per `docs/operations/incidents/README.md`).
- We will revisit moving to **Option A** (always publish a public-safe note for sev0/sev1) once operations are demonstrably stable over multiple full campaign cycles.

## Rationale

- Option B preserves transparency for incidents that matter to user trust and interpretation of the archive.
- It avoids turning the public changelog/status into a high-volume incident feed during an early, fast-changing period.
- Internal incident notes remain the “full fidelity” learning system and can still drive follow-ups and hardening work.

## Alternatives considered

- Option A (always publish public-safe notes for sev0/sev1)
  - Rejected for now due to likely high volume during stabilization; risk of public update fatigue.

## Consequences

### Positive

- Public reporting remains high-signal and user-relevant.
- Internal learning remains intact (incident notes + follow-ups).

### Negative / risks

- Some operator-relevant incidents may not be visible publicly, even if they were sev0/sev1 internally.
  - Mitigation: treat “changes user expectations” as the trigger, not severity alone, and err on the side of communicating when unsure.

## Verification / rollout

- Incident templates and severity rubric should reflect the “Option B” trigger:
  - `docs/operations/incidents/incident-template.md`
  - `docs/operations/incidents/severity.md`
- Ops cadence includes routine doc maintenance so these rules don’t drift:
  - `docs/operations/ops-cadence-checklist.md`

## References

- Incident notes process: `../operations/incidents/README.md`
- Changelog process: `healtharchive-frontend/docs/changelog-process.md`
- Future roadmap note: `../roadmaps/future-roadmap.md`
