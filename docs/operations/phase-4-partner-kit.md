# Phase 4 Partner Kit (draft, public-safe)

Purpose: A lightweight, partner-ready kit that makes it easy to link to
HealthArchive without implying endorsement or medical guidance.

Use this file as the source-of-truth for the 1-page brief, citation handout,
and copy-paste blurbs. Do not include private emails or contact lists here.

---

## 1) One-page brief (draft copy)

### What HealthArchive is

HealthArchive.ca preserves time-stamped snapshots of selected Canadian public
health web pages so changes remain auditable and citable over time.

It provides an archive of what public websites displayed at specific points
in time, plus change tracking to show how wording evolved between editions.

### What it is not

- Not current guidance or medical advice.
- Not an official government site.
- Not affiliated with or endorsed by any public health agency.

### Who it is for

- Researchers and trainees: reproducible citations for historical guidance.
- Journalists and science communicators: accountability timelines.
- Educators: teaching how evidence and guidance evolve.

### What is live now

- Search and browse: `/archive`
- Snapshot viewer: `/snapshot/<id>`
- Changes feed (edition-aware): `/changes`
- Compare view (descriptive diffs): `/compare?to=<id>`
- Digest + RSS: `/digest`
- Methods and scope: `/methods`
- Governance and policies: `/governance`
- Status and impact: `/status`, `/impact`

### Safety posture

This archive is descriptive only. It does not interpret meaning or provide
medical guidance. Always consult the official source for current guidance.

### Project snapshot (fill in from /status)

- Sources tracked: [N]
- Snapshots: [N]
- Pages: [N]
- Latest capture date (UTC): [YYYY-MM-DD]

---

## 2) Distribution blurb (pasteable)

HealthArchive.ca is an independent, non-governmental archive of Canadian
public health web pages. It provides time-stamped snapshots and descriptive
change tracking so researchers, journalists, and educators can audit how
guidance evolves over time. It is not current guidance or medical advice.

Suggested links:

- Digest (RSS + overview): https://www.healtharchive.ca/digest
- Changes feed: https://www.healtharchive.ca/changes
- Methods and scope: https://www.healtharchive.ca/methods

---

## 3) Citation guidance (handout draft)

### Cite a snapshot

HealthArchive.ca Project. "<Page title>" (snapshot from <capture date/time>).
Archived copy of <source organization> web page (<original URL>). Accessed
<access date>. Available from: <HealthArchive snapshot URL>.

Example:

HealthArchive.ca Project. "COVID-19 epidemiology update: Canada" (snapshot
from 2025-02-15 00:00 UTC). Archived copy of Public Health Agency of Canada
web page (https://www.canada.ca/...). Accessed 2025-12-03. Available from:
https://www.healtharchive.ca/snapshot/12345.

### Cite a compare view

HealthArchive.ca Project. "Comparison of archived captures" (from snapshot
<ID A> to snapshot <ID B>). Archived copies of <source organization> web page
(<original URL>). Accessed <access date>. Available from:
https://www.healtharchive.ca/compare?from=<ID A>&to=<ID B>.

Notes:

- Compare views are descriptive diffs, not interpretations.
- Always cite the specific snapshot IDs and capture timestamps shown on the
  compare page.

---

## 4) Screenshot checklist (for partner kit)

Save files with consistent names so they can be attached to outreach emails.

- 01-home.png (Home page with "What this is/is not" block)
- 02-archive.png (/archive with search + filters)
- 03-snapshot.png (snapshot metadata + report link)
- 04-changes.png (/changes feed)
- 05-compare.png (/compare?to=<real id>, shows disclaimer)
- 06-digest.png (/digest with RSS links)
- 07-status.png (/status metrics)
- 08-impact.png (/impact monthly report)

---

## 5) RSS links (reference)

Global RSS:

- https://api.healtharchive.ca/api/changes/rss

Per-source RSS (replace <source> with code, e.g., hc, phac, cihr):

- https://api.healtharchive.ca/api/changes/rss?source=<source>

---

## 6) Notes for partners

- HealthArchive is an archival record, not a guidance provider.
- Please avoid phrasing that implies endorsement or official status.
- Preferred language: "archive", "snapshots", "change tracking",
  "auditability", "reproducibility".
