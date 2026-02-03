# Adoption signals playbook (quarterly)

Goal: record lightweight, public-safe “is anyone using this?” signals without storing private contact details.

Canonical references:

- Template: `../../../_templates/adoption-signals-log-template.md`
- Ops roadmap (remaining external work): `../../healtharchive-ops-roadmap.md`

## Procedure (high level)

1. Create a new dated entry using `../../../_templates/adoption-signals-log-template.md`.
2. Store it on the VPS under:
   - `/srv/healtharchive/ops/adoption/`

Rules:

- Links + aggregate counts only.
- No private emails, names, or identifying details unless permission is explicit and documented elsewhere.

## What “done” means

- A dated adoption signals entry exists under `/srv/healtharchive/ops/adoption/`.
