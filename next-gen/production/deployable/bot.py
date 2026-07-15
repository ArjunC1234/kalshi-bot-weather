"""Next-gen autonomous Kalshi weather demo bot."""

from __future__ import annotations

import argparse
import json

from live_strategy import add_cli_args, config_from_env, run_daemon, run_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kalshi weather demo trading bot")
    commands = parser.add_subparsers(dest="command", required=True)
    once = commands.add_parser("once", help="run one signal/order cycle")
    add_cli_args(once)
    daemon = commands.add_parser("daemon", help="run continuous signal/order loop")
    add_cli_args(daemon)
    commands.add_parser("status", help="check runtime configuration and account access")
    args = parser.parse_args(argv)
    config = config_from_env()
    if args.command == "status":
        print(json.dumps(run_once(config, dry_run=True), indent=2, default=str))
        return 0
    if args.command == "once":
        dry_run = args.dry_run or not args.place_orders
        print(json.dumps(run_once(config, dry_run=dry_run), indent=2, default=str))
        return 0
    if args.command == "daemon":
        dry_run = args.dry_run or not args.place_orders
        run_daemon(config, dry_run=dry_run)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
