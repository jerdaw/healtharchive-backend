# GitHub Actions → VPS deploy (optional, manual)

This repo supports a **manual**, GitHub-triggered deploy workflow:

- Workflow: `.github/workflows/vps-deploy.yml`
- Remote deploy script (runs on the VPS): `scripts/vps-deploy.sh`

This is intentionally **optional** because it adds infrastructure complexity
(secrets + sudo posture) and this project has no staging backend.

## Why Tailscale is required

Production is designed for **Tailscale-only SSH** (no public SSH).

If you want GitHub Actions to deploy, the GitHub runner must join your tailnet
and then SSH to the VPS using its Tailscale IP.

## 0) Preconditions (VPS)

- Repo is deployed at: `/opt/healtharchive-backend`
- Venv exists at: `/opt/healtharchive-backend/.venv`
- `scripts/vps-deploy.sh` works when run manually on the VPS
- You have a stable Tailscale IP for the VPS (e.g. `100.x.y.z`)

## 1) Choose the SSH user

Lowest-complexity option (recommended for solo-dev):

- Use the existing backend user: `haadmin`

More locked-down option (more setup):

- Create a dedicated deploy user (e.g. `ha-deploy`) and grant only the minimum
  permissions needed to:
  - read/write `/opt/healtharchive-backend`,
  - run `pip` and `alembic` inside the venv,
  - restart services via sudo (see below).

## 2) Configure passwordless sudo for deploys (required)

GitHub Actions cannot type a sudo password. The workflow will fail unless the
SSH user can run the required `systemctl` commands without a password.

Create a sudoers drop-in on the VPS (example for `haadmin`):

```bash
sudo visudo -f /etc/sudoers.d/healtharchive-deploy
```

Paste (adjust user and units if you differ):

```sudoers
Cmnd_Alias HA_SYSTEMCTL = \
  /usr/bin/systemctl daemon-reload, \
  /usr/bin/systemctl restart healtharchive-api healtharchive-worker, \
  /usr/bin/systemctl status healtharchive-api healtharchive-worker --no-pager -l, \
  /usr/bin/systemctl restart healtharchive-replay.service

haadmin ALL=(root) NOPASSWD: HA_SYSTEMCTL
```

Validate:

```bash
sudo -u haadmin sudo -n systemctl daemon-reload
sudo -u haadmin sudo -n systemctl status healtharchive-api --no-pager -l >/dev/null
```

If either command prompts or fails, the workflow will not be able to deploy.

## 3) Create a deploy SSH keypair

On your local machine (not in the repo):

```bash
ssh-keygen -t ed25519 -C "healtharchive-github-deploy" -f ./healtharchive_github_deploy
```

Install the public key on the VPS for the chosen user:

```bash
sudo -u haadmin install -d -m 0700 ~haadmin/.ssh
sudo -u haadmin install -m 0600 /dev/null ~haadmin/.ssh/authorized_keys
sudo -u haadmin bash -lc 'cat >> ~/.ssh/authorized_keys' < ./healtharchive_github_deploy.pub
```

Keep the private key secret; it will be stored in GitHub Secrets.

## 4) Capture the VPS host key (known_hosts)

From a machine that can reach the VPS over Tailscale:

```bash
ssh-keyscan -t ed25519 <VPS_TAILSCALE_IP>
```

Copy the output line(s). You will store them as a GitHub secret so the workflow
can use `StrictHostKeyChecking=yes`.

## 5) Create a Tailscale auth key for GitHub Actions

In Tailscale admin:

- Create an auth key suitable for GitHub Actions.
- Prefer a key with:
  - a short expiry,
  - minimal scope,
  - and no device sharing.

Store the auth key as a GitHub Secret.

## 6) Add GitHub Secrets

GitHub → Repo → Settings → Secrets and variables → Actions → New repository secret

Required secrets (names expected by `.github/workflows/vps-deploy.yml`):

- `TAILSCALE_AUTHKEY` — Tailscale auth key for the runner
- `VPS_TAILSCALE_IP` — VPS Tailscale IP (e.g. `100.x.y.z`)
- `VPS_SSH_USER` — SSH user (e.g. `haadmin`)
- `VPS_SSH_PRIVATE_KEY` — the **private** key contents from `healtharchive_github_deploy`
- `VPS_SSH_KNOWN_HOSTS` — output from `ssh-keyscan` (known_hosts lines)

## 7) Run the workflow

GitHub → Actions → “VPS Deploy (manual)” → Run workflow

Recommended first run:

- `apply = false` (dry-run only)

To deploy:

- `apply = true`
- `confirm = DEPLOY`
- optionally `restart_replay = true`

The workflow always runs a dry-run first, then (if apply) runs the real deploy.

## 8) Rollback

This workflow deploys “latest `main`” by fast-forward pull on the VPS.

Rollback is intentionally manual:

- SSH to VPS, then deploy the previous known-good SHA using your runbook.

If you later want “deploy pinned SHA” from GitHub, adjust `scripts/vps-deploy.sh`
and the workflow together; beware that editable installs use whatever is
currently checked out on disk.

## 9) How to disable quickly

- Disable the workflow by renaming/deleting `.github/workflows/vps-deploy.yml`, or
- Revoke:
  - the Tailscale auth key, and/or
  - the deploy SSH key (remove from `authorized_keys`), and/or
  - the GitHub secrets.
