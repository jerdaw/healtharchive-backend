# Ops UI friction log (template)

Goal: decide whether a bespoke admin/ops UI is worth building by capturing real operator pain over time.

Guideline:

- Use Grafana dashboards + existing JSON admin endpoints first.
- Only build a bespoke UI if it clearly reduces recurring toil.

---

## Entry

**Date (UTC):**

**Operator:**

**Context:**

- What were you trying to do? (triage, investigate outage, verify deploy, investigate search quality, etc.)
- What triggered it? (alert, user report, scheduled check, curiosity)

**What worked well:**

- What was quick/easy? (dashboard answered it, existing playbook, one command)

**Friction / pain:**

- What took longer than it should?
- Did you need SSH for more than port-forwarding?
- Did you need to manually handle tokens/headers?
- Did you need to “hunt” for the right endpoint/query?

**Impact:**

- Time spent (rough): `X minutes`
- Frequency: one-off / weekly / daily / during incidents
- Risk: low / medium / high (chance of operator mistake)

**Workaround used (today):**

- Command(s) / link(s) / dashboard(s):

**Proposed improvement:**

- Dashboard improvement? (new panel/table/link)
- Script improvement? (new helper, safer defaults)
- Doc/playbook improvement?
- Is a bespoke UI actually required? Why?

**Decision signal:**

- If this happened again, would a bespoke UI save meaningful time?
