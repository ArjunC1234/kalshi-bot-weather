"""Compare a saved quote with its original payload without modifying remote data."""

import argparse
import json
from pathlib import Path

from libs.supabase_client import SupabaseClient
from scripts.hit80_14d_research import _load_json_gz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    markets = _load_json_gz(args.data / "market_snapshots.json.gz")
    weather = _load_json_gz(args.data / "weather_snapshots.json.gz")
    lookup = {(r["event_ticker"], r["snapshot_time_utc"]): r for r in weather}
    candidate = next(r for r in markets if .01 < (r.get("no_ask_size") or 0) < 1
                     and r["target_date"] >= "2026-08-04")
    raw_id = lookup[(candidate["event_ticker"], candidate["snapshot_time_utc"])]["source_payload_ids"]["kalshi_open_markets"]
    client = SupabaseClient.from_env()
    metadata = client.select("raw_payloads", {"raw_payload_id": f"eq.{raw_id}",
                                            "select": "storage_path,received_at_utc"})[0]
    payload = client.download_json_gz(metadata["storage_path"])
    print("payload keys:", list(payload))
    if "body" in payload:
        payload = payload["body"]
    if "response" in payload:
        payload = payload["response"]
    raw = next(r for r in payload["markets"] if r["ticker"] == candidate["market_ticker"])
    keys = ["yes_bid_size", "yes_bid_size_fp", "yes_ask_size", "yes_ask_size_fp", "no_ask_size", "no_ask_size_fp"]
    print(json.dumps({"market": candidate["market_ticker"], "snapshot": candidate["snapshot_time_utc"],
                      "received": metadata["received_at_utc"], "stored": {k: candidate.get(k) for k in keys},
                      "raw": {k: raw.get(k) for k in keys}}, indent=2))


if __name__ == "__main__":
    main()
