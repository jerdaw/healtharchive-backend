# Escalation Procedures

**Last Updated:** 2026-01-18
**Status:** Active

This document defines how to categorize, escalate, and respond to incidents affecting the HealthArchive production environment.

## 1. Severity Levels

We categorize incidents into four levels based on impact and urgency.

| Level | Definition | Response Time | Actions |
| :--- | :--- | :--- | :--- |
| **Sev0** | **Critical Outage / Data Loss**<br>System is totally unusable, or confirmed data loss is occurring. | **Immediate** | 1. Stop all non-recovery work.<br>2. Notify stakeholders (if any).<br>3. Initiate [Disaster Recovery](../deployment/disaster-recovery.md). |
| **Sev1** | **Major Degradation**<br>Core features (Search, API) are broken or extremely slow. User impact is high. | **< 1 Hour** | 1. Engage Primary On-Call.<br>2. Investigate immediately.<br>3. Deploy hotfix or rollback. |
| **Sev2** | **Partial Degradation**<br>Secondary features (e.g., Replay) broken, or performance issues with workarounds. | **< 4 Hours** | 1. Log incident.<br>2. Investigate within business hours.<br>3. Schedule fix for next release window. |
| **Sev3** | **Minor Issue**<br>Trivial bugs, cosmetic issues, or single-page failures. No broad user impact. | **< 24 Hours** | 1. Log ticket/issue.<br>2. Prioritize in normal development backlog. |

## 2. Escalation Path

### Current State: Single Operator
In the current single-maintainer topology, the escalation path is flat.

1.  **Primary:** Operator (You) - Responsible for all triage and resolution.
2.  **Backup:** None (Bus factor = 1).
    - *Mitigation:* Comprehensive [Runbooks](../deployment/production-single-vps.md) and [Disaster Recovery](../deployment/disaster-recovery.md) docs to allow a skilled third party to recover the system using "Break-Glass" credentials if the primary operator is incapacitated.

### Future State: Multi-Operator
When the team grows, follow this hierarchy:

1.  **Level 1 (On-Call):** Triage, immediate mitigation, and initial investigation.
2.  **Level 2 (Secondary/Backup):** Deep dive debugging, code fixes, and complex recovery.
3.  **Level 3 (Project Lead):** Strategic decisions (e.g., data loss acceptance, major architecture rollback).

## 3. DRI Assignments (Directly Responsible Individuals)

Since we largely operate as a single unit, the **Operator** is the DRI for all areas. This matrix serves as a template for future delegation.

| Area | DRI | Responsibilities |
| :--- | :--- | :--- |
| **Backend API** | Operator | FastAPI availability, performance, response correctness. |
| **Worker / Crawls** | Operator | Job scheduling, zimit/warcio execution, tiering to storage. |
| **Database** | Operator | PostgreSQL uptime, backup verification, schema migrations. |
| **Storage / WARC** | Operator | Disk space management, Storage Box connectivity, manifest integrity. |
| **Replay Service** | Operator | `pywb` availability and indexing health. |
| **Infrastructure** | Operator | VPS provisioning, OS updates, systemd maintenance, Tailscale. |

## 4. Contact Information Storage

For security reasons, **do not store phone numbers or sensitive access codes in this git repository.**

### Production Contact list
Store a secure, read-only file on the production VPS for emergency reference:

- **Path:** `/etc/healtharchive/contacts.env`
- **Permissions:** `600` (root/owner only)
- **Format:** Key-Value pairs

```bash
# Example content for /etc/healtharchive/contacts.env
OPERATOR_PHONE="+1-555-0100"
OPERATOR_EMAIL="admin@healtharchive.ca"
SECONDARY_CONTACT_PHONE="+1-555-0101" # Backup contact (if any)
HETZNER_SUPPORT_PIN="12345"
NAMECHEAP_SUPPORT_PIN="67890"
```

### Personal Backup
Mirror this information in your password manager (e.g., 1Password, Bitwarden) under a secure note titled "HealthArchive Emergency Contacts".

## 5. Break-Glass Procedures

Quick-reference steps for common critical failures where normal access or services are blocked.

### A. API Unresponsive (HTTP 502/503/Timeout)
1.  **Access:** SSH to VPS via Tailscale (`ssh haadmin@100.x.y.z`).
2.  **Status:** Check if the service is running.
    ```bash
    username@host:~$ systemctl status healtharchive-api
    ```
3.  **Logs:** specific error messages?
    ```bash
    username@host:~$ journalctl -u healtharchive-api -n 100
    ```
4.  **Action:** Restart the service.
    ```bash
    username@host:~$ sudo systemctl restart healtharchive-api
    ```
5.  **Escalation:** If restart fails or immediately crashes, check Database connectivity (see B).

### B. Database Unreachable
1.  **Status:** Is Postgres running?
    ```bash
    username@host:~$ systemctl status postgresql
    ```
2.  **Resources:** Is disk full?
    ```bash
    username@host:~$ df -h
    ```
3.  **Logs:**
    ```bash
    username@host:~$ journalctl -u postgresql -n 100
    ```
4.  **Action:** Restart Postgres.
    ```bash
    username@host:~$ sudo systemctl restart postgresql
    ```
5.  **Escalation:** If database won't start due to corruption, proceed to [Disaster Recovery Scenario B](../deployment/disaster-recovery.md#2-database-intact-restoration-scenario-b).

### C. VPS Unreachable (SSH Down)
1.  **Check Network:** Try accessing via different Tailscale node or public IP (if SSH open/testing).
2.  **Console Access:** Log in to **Hetzner Cloud Console** > Select Server > **Console**.
    - This bypasses network/SSH config issues.
3.  **Reboot:** Use the Hetzner "Power" menu to force a reboot ACPI or hard reset if the OS is frozen.
4.  **Escalation:** If the server is deleted or hardware failed, proceed to [Disaster Recovery Scenario A](../deployment/disaster-recovery.md#1-vps-complete-restoration-scenario-a).

## 6. Handoff Procedures

When transferring responsibility (e.g., vacation coverage):

1.  **Sync:** Verify current system health (dashboards, logs).
2.  **Access:** Confirm backup operator has valid SSH/Tailscale access.
3.  **Docs:** Ensure emergency contact info is accessible to the backup.
4.  **Notify:** Inform any stakeholders (if applicable) of the active operator change.
