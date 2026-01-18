# Documentation Templates

This directory contains templates for creating consistent documentation across the project.

## Available Templates

| Template | Purpose | Destination |
|----------|---------|-------------|
| `runbook-template.md` | Deployment/operational procedures | `docs/deployment/` |
| `playbook-template.md` | Task-oriented checklists | `docs/operations/playbooks/` or `docs/development/playbooks/` |
| `incident-template.md` | Incident postmortems | `docs/operations/incidents/` |
| `decision-template.md` | Architectural/policy decisions | `docs/decisions/` |
| `restore-test-log-template.md` | Quarterly restore test logs | `/srv/healtharchive/ops/restore-tests/` (VPS) |
| `adoption-signals-log-template.md` | Quarterly adoption signals | `/srv/healtharchive/ops/adoption/` (VPS) |
| `mentions-log-template.md` | Public mentions log entries | `docs/operations/mentions-log.md` |
| `ops-ui-friction-log-template.md` | Internal friction tracking | Local ops notes (not git) |

## How to Use

1. **Copy** the appropriate template to the destination directory
2. **Rename** the file (remove `-template` suffix)
3. **Fill in** all sections with your content
4. **Update** the directory's `README.md` index to include the new doc
5. **Add to navigation** in `mkdocs.yml` if the doc is critical/frequently accessed

## Template Conventions

### Runbooks
- Purpose: Step-by-step operational procedures
- Audience: Operators with appropriate access
- Structure: Purpose, Scope, Preconditions, Architecture, Procedure, Verification, Rollback, Troubleshooting, References

### Playbooks
- Purpose: Short task-oriented checklists
- Audience: Operators performing recurring work
- Structure: Purpose, Preconditions, Steps, Verification, Safety, References

### Incident Notes
- Purpose: Lightweight postmortems for operational learning
- Audience: Internal operators
- Structure: Metadata, Timeline, Impact, Root Cause, Resolution, Follow-ups, References

### Decision Records
- Purpose: Document high-stakes architectural/policy choices
- Audience: All contributors
- Structure: Context, Decision, Rationale, Alternatives, Consequences, Verification, References

## Maintenance

- Templates should be updated when patterns evolve
- Keep templates minimal and focused on structure
- Avoid prescriptive content that changes frequently
- Templates are excluded from the published docs site navigation but remain in the repo

## References

- Documentation guidelines: `../documentation-guidelines.md`
- Quality bar requirements: See "Definition of Done" in guidelines
