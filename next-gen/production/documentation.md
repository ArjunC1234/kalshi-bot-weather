# Production

Production contains code and scripts intended for the server.

## Layout

- `deployable/` mirrors the folder structure that should exist on the bot server.
- `deploy.ps1` uploads `deployable/` from Windows.
- `deploy.sh` uploads `deployable/` from Unix-like shells.

## Rule

Deploy scripts must upload only the contents of `next-gen/production/deployable/`.
