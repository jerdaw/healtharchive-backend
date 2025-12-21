# HealthArchive.ca - Citation handout (Phase 4)

Purpose: A pragmatic, stable way to cite archived HealthArchive snapshots and
compare views.

These citations refer to archived content, not current guidance.

Public page: https://www.healtharchive.ca/cite

---

## 1) Cite a snapshot

Recommended format:

HealthArchive.ca Project. "<Page title>" (snapshot from <capture date/time>).
Archived copy of <source organization> web page (<original URL>). Accessed
<access date>. Available from: <HealthArchive snapshot URL>.

Example:

HealthArchive.ca Project. "COVID-19 epidemiology update: Canada" (snapshot
from 2025-02-15 00:00 UTC). Archived copy of Public Health Agency of Canada
web page (https://www.canada.ca/...). Accessed 2025-12-03. Available from:
https://www.healtharchive.ca/snapshot/12345.

Notes:

- Use the exact snapshot URL you visited.
- Use the capture timestamp shown on the snapshot page (UTC).
- Always cite the original URL as well.

---

## 2) Cite a compare view

Compare views highlight descriptive text differences between two archived
captures of the same page.

Recommended format:

HealthArchive.ca Project. "Comparison of archived captures" (from snapshot
<ID A> to snapshot <ID B>). Archived copies of <source organization> web page
(<original URL>). Accessed <access date>. Available from:
https://www.healtharchive.ca/compare?from=<ID A>&to=<ID B>.

Notes:

- Compare output is descriptive only and does not interpret meaning.
- Always record both snapshot IDs and the capture timestamps shown on the
  compare page.

---

## 3) Where to find the citation fields

On `/snapshot/<id>` you can find:

- Page title
- Capture date/time (UTC)
- Source organization
- Original URL
- Snapshot URL (the page you're on)

On `/compare?from=<id>&to=<id>` you can find:

- Both snapshot IDs
- Both capture date/times (UTC)
- The original URL
- A descriptive change summary
