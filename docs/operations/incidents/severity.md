# Incident severity rubric (internal)

Severity is a shared shorthand for **priority and urgency**, not blame.

Use this rubric to decide how quickly to respond, what to pause, and what level
of documentation/verification is appropriate.

Notes:

- Severity can change as you learn more; update the incident note accordingly.
- When in doubt, start higher and downgrade later.
- Data integrity risk should push severity up.

---

## sev0 — Critical outage / integrity / security

Criteria (any one is enough):

- Public site/API is effectively down for most users, or returning incorrect/unsafe data.
- Credible data integrity compromise (corruption, missing/cross-linked WARCs, replay integrity loss).
- Security incident (credential leak, unauthorized access, suspicious behavior).

Response expectations:

- Immediate response.
- Prefer conservative actions that preserve integrity (pause/stop destructive jobs).
- Capture a complete timeline and include post-incident verification.
- Public note is optional but recommended when it changes user expectations (outage, integrity risk, security posture, policy change). Keep it public-safe (no sensitive details).

---

## sev1 — Major degradation / time-critical capture risk

Criteria (any one is enough):

- Public site/API is usable but severely degraded (major routes broken, errors widespread).
- Annual capture campaign is blocked or likely to miss the window (or lose meaningful coverage) without intervention.
- Storage/mount instability that threatens crawl/indexing continuity even if the public surface is OK.

Response expectations:

- Same-day response.
- Record the recovery steps precisely (commands + what they changed).
- Public note is optional but recommended when it changes user expectations (outage/degradation, integrity risk, public posture). Keep it public-safe (no sensitive details).

---

## sev2 — Partial degradation / contained pipeline failure

Criteria (typical examples):

- One source crawl repeatedly failing but others are healthy.
- Crawl/indexing slowdown or intermittent errors with a known workaround.
- Non-critical automation failing (metrics, watchdogs, timers) with manual fallback.

Response expectations:

- Next-business-day response is usually acceptable.
- Document what happened and track follow-ups that reduce repeat issues.

---

## sev3 — Minor issue / operational friction

Criteria (typical examples):

- Internal-only issue with low urgency and a simple workaround.
- Documentation gaps discovered during ops.
- Cosmetic or non-impactful errors that should still be cleaned up.

Response expectations:

- Fix opportunistically.
- Still record the incident if it required manual intervention or could recur.
