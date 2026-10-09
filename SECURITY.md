# Security Policy

## Secrets

Do not commit live credentials, `.env` files, API keys, private keys, database URLs, service-role tokens, or production logs.

This repository uses ignored local files for runtime secrets:

- `.env`
- `*.env`
- `*.pem`
- `next-gen/production/deployable/kalshi_private_key.local.pem`

Use checked-in example files only as templates.

## Before Publishing Publicly

If any real credential was ever committed to git history, rotate it before making the repository public. Deleting the file from the latest commit prevents future checkout exposure, but it does not remove the secret from earlier commits.

## Trading And Research Disclaimer

This repository contains research code, offline analysis, and paper-trading experiments. It should not be read as financial advice, a profitability claim, or authorization to deploy live trading.
