# Production

`production/` contains code and scripts intended for the server. It is not where local backtests, model reports, Trends output, or strategy experiments belong.

## Layout

- `deployable/`: mirrors the folder structure that should exist on the droplet at `/opt/kalshi-weather-next-gen`.
- `deploy.ps1`: Windows deploy script.
- `deploy.sh`: Unix-like deploy script.

## Current Production Scope

The active production component is collector v3. It runs hourly, captures immutable market/weather facts, writes local spools first, uploads raw payloads to Supabase Storage, inserts compact fact rows into Postgres, checks settlements, and ingests final NWS high labels when available.

Production should not run model training, Trends, strategy simulations, or report generation.

## Windows Deploy

Run from PowerShell in this folder:

```powershell
.\deploy.ps1
```

The script loads `$env:USERPROFILE\.ssh\id_ed25519` into `ssh-agent` by default, so you should only need to type the SSH key passphrase once. If your key is elsewhere:

```powershell
.\deploy.ps1 -KeyPath "C:\Users\Arjun\.ssh\your_key_name"
```

Use `-NoAgent` only if you intentionally do not want the script to use `ssh-agent`.

## Server Verification

After deploying, SSH into the droplet and run:

```bash
cd /opt/kalshi-weather-next-gen
source .venv/bin/activate
python collector/collector.py status
python collector/collector.py collect-once --dry-run
```

Check systemd:

```bash
systemctl status kalshi-weather-collector-v3.timer --no-pager
journalctl -u kalshi-weather-collector-v3.service -n 100 --no-pager
```

## Rules

- Deploy scripts must upload only the contents of `next-gen/production/deployable/`.
- Do not upload local `data/`, `reports/`, `models/`, or legacy folders.
- Do not commit real `.env` files or private keys.
- Keep collector code standalone enough to run on the droplet without importing local project modules outside `deployable/`.
