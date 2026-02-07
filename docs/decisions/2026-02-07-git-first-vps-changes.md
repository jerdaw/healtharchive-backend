# Decision: Git-first VPS changes; keep /opt checkout clean (2026-02-07)

Status: accepted

## Context

- The production VPS uses a git checkout at `/opt/healtharchive-backend` as the deploy source of truth.
- `scripts/vps-deploy.sh` (and related automation) intentionally refuses a dirty working tree because it makes deploys non-reproducible and breaks rollback semantics.
- Operator friction was observed when ad-hoc `scp`-copied scripts (or local edits) landed in `/opt/healtharchive-backend`, causing:
  - dirty-tree deploy failures,
  - configuration drift and “it works on this machine” behavior,
  - unclear provenance of production changes.
- This project is currently solo-operated; the lowest-cost way to stay sane is to make the “right path” the easiest path.

## Decision

- We will treat `/opt/healtharchive-backend` as a **git-managed** deploy artifact only:
  - no ad-hoc `scp` into that working tree,
  - no uncommitted production edits in that tree.
- We will distribute operator helpers (deploy wrappers, diagnostics scripts) via **git**:
  - land in repo,
  - deploy/pull to VPS,
  - optionally install to `/usr/local/bin` from the pulled checkout.
- We will use a clearly labeled backend-only deploy mode only when the public frontend is externally broken (example: Vercel `402`), and keep the default deploy gate strict.

## Rationale

- Git-based deploys preserve provenance, make rollbacks deterministic, and keep “what is running” legible.
- A clean `/opt` checkout is a hard precondition for reliable automation and incident response.
- Avoiding `scp` eliminates a common source of silent drift and broken deploy gates.

## Alternatives considered

- Continue allowing ad-hoc `scp` into `/opt/healtharchive-backend` — rejected: leads to dirty-tree deploy failures and untracked production drift.
- Maintain a separate “ops scripts” copy outside git (manual sync) — rejected: additional moving parts, worse provenance, easy to forget.

## Consequences

### Positive

- Deploys remain reproducible and rollbackable.
- Automation that depends on “repo state” behaves predictably.
- Operator workflow becomes simpler: `git pull`, then run the standard command.

### Negative / risks

- Urgent hotfixes require a git change (or temporary workaround outside `/opt`), which can feel slower in the moment.
- Operators must resist the temptation to “just scp a script in”.

## Verification / rollout

- Verification:
  - `cd /opt/healtharchive-backend && git status --porcelain` is empty before deploys.
  - Use `./scripts/vps-hetzdeploy.sh` (or installed `/usr/local/bin/hetzdeploy`) for routine deploys.
- Rollback:
  - `git log --oneline` to identify the previous known-good SHA.
  - `git checkout <sha>` (or `git revert` in repo + redeploy) depending on the change type.

## References

- Related canonical docs:
  - `docs/operations/playbooks/core/deploy-and-verify.md`
  - `docs/deployment/production-single-vps.md`
- Related implementation plans:
  - `docs/planning/implemented/2026-02-07-deploy-workflow-hardening.md`
- Related scripts:
  - `scripts/vps-deploy.sh`
  - `scripts/vps-hetzdeploy.sh`
