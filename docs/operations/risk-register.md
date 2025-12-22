# Risk Register (internal)

Track the top operational risks and mitigations.

- **Misinterpretation risk (archive mistaken for current guidance)**
  - Mitigation: strong disclaimers; never add “interpretation” features; keep high-risk pages (`/browse`, `/snapshot`) explicit.
- **PHI submission risk (issue reports)**
  - Mitigation: clear warnings; minimize storage; admin-only access; delete/redact if PHI appears.
- **Proxy/CORS misuse risk**
  - Mitigation: keep the frontend same-origin report proxy narrow; do not turn it into a general proxy; keep backend CORS allowlist strict.
- **Single-VPS availability risk**
  - Mitigation: backups + restore tests; conservative automation caps; disk monitoring; clear rollback procedures.
- **Export integrity / reproducibility risk**
  - Mitigation: checksums + manifest; stable ordering/pagination; version fields (`diff_version`, `normalization_version`); avoid rewriting releases.
