# Outreach + verification playbook (ongoing)

Goal: run external outreach and verification work **without** storing private contact details in git.

Canonical references:

- Outreach email templates: `../outreach-templates.md`
- Partner kit (links + screenshot checklist): `../partner-kit.md`
- Verification packet outline: `../verification-packet.md`
- Mentions log (public-safe, link-only): `../mentions-log.md`
- Data handling & retention rules: `../data-handling-retention.md`
- Adoption signals (quarterly, VPS-only): `adoption-signals.md`

## Rules (hard)

- Never store emails, phone numbers, names, or private notes in git.
- Public logs must be **link-only** and **permission-aware**:
  - If permission to name is unclear, use “Pending” and keep the name out of public copy.

## Procedure

### 1) Create a private tracker (operator-only; not in git)

Pick one:

- A password manager note (preferred).
- A local spreadsheet in a folder outside the repo (e.g. `~/HealthArchive-private/outreach.xlsx`).
- A private doc in your personal notes system.

Suggested fields:

- `date_first_contacted_utc`
- `name` / `role` / `org` (private)
- `contact_channel` (private)
- `why_them`
- `template_used` (A/B/C)
- `status` (no response / declined / interested / accepted)
- `followup_1_sent_utc`, `followup_2_sent_utc`
- `public_link` (only if it exists)
- `permission_to_name` (yes/no/pending) + date confirmed (private)

### 2) Prepare partner-ready assets (public)

- Confirm these pages are accurate and up-to-date:
  - `https://www.healtharchive.ca/brief`
  - `https://www.healtharchive.ca/cite`
- (Optional) Capture screenshots using `../partner-kit.md` so you can attach them.

### 3) Build a target list (operator-only)

Start with a small batch (e.g., 10–20), split between:

- Distribution partners (libraries / digital scholarship resource pages).
- Research / teaching partners.
- Journalism / communication partners.
- Verifier candidates (librarian / researcher / editor).

### 4) Send outreach (operator-only)

- Use `../outreach-templates.md` and customize only what’s needed:
  - recipient name
  - why this is relevant to them
  - the single best link to include (usually `/digest` or `/changes`)
- Follow-up cadence:
  - follow-up #1 at ~1 week
  - follow-up #2 at ~2 weeks (final)

### 5) Update the public-safe mentions log (git) when appropriate

Only when there is a public link (and/or explicit permission to name), add an entry to:

- `../mentions-log.md`

### 6) Run the verifier workflow (operator-only)

- Send `../verification-packet.md` to the verifier.
- Ask explicitly:
  - permission to name them publicly (yes/no)
  - preferred wording (if any)
- If they grant permission and there is a public link (or they agree to be listed), record it in:
  - `../mentions-log.md`

### 7) Quarterly adoption signals (VPS-only; public-safe)

Run the adoption signals playbook and store the entry on the VPS:

- `adoption-signals.md`

## What “done” means (Phase 4)

- Private tracker exists outside git.
- At least one outreach batch is sent (with follow-ups scheduled).
- Mentions log exists and is updated only with public links and permission-aware entries.
