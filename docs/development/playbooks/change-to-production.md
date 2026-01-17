# Change → production workflow (solo-fast)

Goal: ship a change safely while keeping “green main” as the deploy gate.

Canonical references:

- Docs guidelines: `../../documentation-guidelines.md`
- Monitoring/CI gate: `../../operations/monitoring-and-ci-checklist.md`
- Deploy playbook (VPS): `../../operations/playbooks/deploy-and-verify.md`

## Workflow

1. Make the change locally.
2. Run checks:
   - `make check`
3. Commit and push.
4. Wait for CI to pass on `main`.
5. Deploy on the VPS using the deploy playbook.

## Cross-repo guardrails

- If you add/change a user-facing frontend route that is part of the production “public surface”, update:
  - `scripts/verify_public_surface.py`
  - Frontend bilingual rules (in the frontend repo): https://github.com/jerdaw/healtharchive-frontend/blob/main/docs/development/bilingual-dev-guide.md
