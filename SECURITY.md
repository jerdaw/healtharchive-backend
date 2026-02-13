# Security Policy

## Supported Versions

Only the latest release on the `main` branch is actively supported with security updates.

## Reporting a Vulnerability

**Please do not open public issues for security vulnerabilities.**

We take security seriously and appreciate responsible disclosure. To report a vulnerability:

1. **Preferred**: Use [GitHub private vulnerability reporting](https://github.com/jerdaw/healtharchive-backend/security/advisories/new) to submit a report directly through GitHub.
2. **Alternative**: Email `security@healtharchive.ca` with details of the vulnerability.

### What to Include

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof of concept
- Any relevant logs, screenshots, or configuration details
- Your suggested severity assessment (if any)

### Response Timeline

- **Acknowledgment**: Within 48 hours of receipt
- **Assessment**: Within 7 days we will provide an initial assessment of the report
- **Resolution**: Depends on severity; critical issues are prioritized for immediate fix

## Scope

The following are in scope for security reports:

- The HealthArchive Backend API (`src/ha_backend/`)
- Authentication and authorization mechanisms
- Data handling and storage
- Dependencies with known vulnerabilities

The following are **out of scope**:

- The production VPS infrastructure (report to the hosting provider)
- Denial-of-service attacks
- Social engineering

## Safe Harbor

We support safe harbor for security researchers who:

- Make a good faith effort to avoid privacy violations, data destruction, or service disruption
- Only interact with accounts you own or with explicit permission
- Do not exploit a vulnerability beyond what is necessary to confirm it
- Report vulnerabilities promptly and do not disclose publicly before a fix is available

We will not pursue legal action against researchers who follow these guidelines.

## Disclosure Policy

- We aim to fix confirmed vulnerabilities before public disclosure.
- We will coordinate disclosure timing with the reporter.
- Credit will be given to reporters unless they prefer to remain anonymous.
