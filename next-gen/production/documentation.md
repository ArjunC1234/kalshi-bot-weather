# Production

Production contains code and scripts intended for the server.

## Layout

- `deployable/` mirrors the folder structure that should exist on the bot server.
- `deploy.ps1` uploads `deployable/` from Windows.
- `deploy.sh` uploads `deployable/` from Unix-like shells.

## Windows Deploy

Run from PowerShell:

```powershell
.\deploy.ps1
```

The script loads `$env:USERPROFILE\.ssh\id_ed25519` into `ssh-agent` by default, so you should only need to type the SSH key passphrase once. If your key is elsewhere:

```powershell
.\deploy.ps1 -KeyPath "C:\Users\Arjun\.ssh\your_key_name"
```

Use `-NoAgent` only if you intentionally do not want the script to use `ssh-agent`.

## Rule

Deploy scripts must upload only the contents of `next-gen/production/deployable/`.
