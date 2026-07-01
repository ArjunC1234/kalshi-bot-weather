# Demo Kalshi Weather Bot

Standalone demo-trading bot for Kalshi daily-high weather markets.

This folder is intentionally independent from the backtesting/collector code in
the parent project. It can be copied to a server by itself.

## What It Does

- Polls Kalshi, NWS, NWS station observations, and Open-Meteo HRRR every
  configured interval.
- Watches NWS forecast metadata. A city is evaluated only when the NWS daily or
  hourly forecast update signature changes for the selected event.
- Builds a simple NWS + HRRR bracket probability estimate.
- Buys YES on the top bracket only when model probability clears the archived
  YES ask plus the configured edge.
- Syncs live Kalshi demo positions when trading is enabled and tries to sell
  actual held YES contracts when a newer NWS/HRRR update disagrees with the old
  bracket.
- Submits orders to the Kalshi demo API when `TRADE_ENABLED=true`.
- Persists state in `state/state.json` to avoid duplicate orders for the same
  city, event, NWS update, and selected ticker.

## Safety Defaults

By default, this bot does **not** place orders:

```powershell
TRADE_ENABLED=false
```

Set `TRADE_ENABLED=true` only after a dry run shows the expected decisions.
This bot is for `demo.kalshi.co`, not production Kalshi.

## Setup

```powershell
cd demo_server_bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`:

```text
KALSHI_API_KEY_ID=your-demo-api-key-id
KALSHI_PRIVATE_KEY_FILE=C:\path\to\kalshi-demo-private-key.pem
NWS_USER_AGENT=kalshi-weather-demo-bot/0.1 your@email.com
TRADE_ENABLED=false
```

Edit `config.json` for sizing, city filters, interval, and edge threshold.

## Run Once

```powershell
python bot.py --once
```

## Run Continuously

```powershell
python bot.py
```

The default interval is read from `config.json`; currently `20` minutes.

## Server Deployment

Copy this folder to the server, install dependencies, create `.env`, and run:

```bash
python3 bot.py
```

For Linux systemd, edit `systemd/kalshi-weather-demo-bot.service`, then:

```bash
sudo cp systemd/kalshi-weather-demo-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-weather-demo-bot
sudo journalctl -u kalshi-weather-demo-bot -f
```

## Required Environment Variables

- `NWS_USER_AGENT`: Required by NWS. Include app name and contact email.
- `KALSHI_API_KEY_ID`: Demo API key id.
- `KALSHI_PRIVATE_KEY_FILE`: Path to RSA private key PEM for that API key.
- `TRADE_ENABLED`: `true` to place demo orders, anything else to dry-run.

## Important Limits

- This is a demo trading bot, not a proven strategy.
- It uses a simple standalone model, not the full historical backtest stack.
- It does not backfill missed NWS updates.
- It does not guarantee fills, even with IOC limit orders.
- It does not optimize order size.
- It does not trade production Kalshi unless you deliberately change the API
  base URL.
- The local ledger is an audit log. When `TRADE_ENABLED=true`, live Kalshi
  positions are the source of truth for sell quantities.
