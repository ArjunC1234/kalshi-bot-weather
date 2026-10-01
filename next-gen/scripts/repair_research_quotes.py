"""Reconstruct research quote sizes from cached original Kalshi payloads.

Only local derivative exports are written. Download selection uses quote fields,
never outcomes or model profitability. Other rows fail the verified-depth gate.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from libs.io_utils import write_json_gz
from libs.json_utils import write_json
from libs.supabase_client import SupabaseClient
from scripts.hit80_14d_research import _load_json_gz


def quantity(raw, side, leg):
    for key in (f"{side}_{leg}_size_fp", f"{side}_{leg}_size"):
        if raw.get(key) is not None:
            return float(raw[key])
    if side == "no":
        return quantity(raw, "yes", "bid" if leg == "ask" else "ask")
    return None


def retry_reads(client):
    retry = Retry(total=3, backoff_factor=.5, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    client.session.mount("https://", HTTPAdapter(max_retries=retry))
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all-afternoon", action="store_true",
                        help="Verify every climate-hour 14-19 snapshot without quote-price selection")
    selection.add_argument("--all-hours", nargs=2, type=int, metavar=("START", "END"),
                           help="Verify every snapshot in [START, END) climate hours without price selection")
    args = parser.parse_args()
    if args.all_hours and not 0 <= args.all_hours[0] < args.all_hours[1] <= 24:
        parser.error("--all-hours must satisfy 0 <= START < END <= 24")
    if args.output.resolve() == args.data.resolve():
        raise ValueError("Use a derivative export; do not overwrite original facts")
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    markets = _load_json_gz(args.data / "market_snapshots.json.gz")
    weather = _load_json_gz(args.data / "weather_snapshots.json.gz")
    wx = {(r["event_ticker"], r["snapshot_time_utc"]): r for r in weather}
    needed = {}
    for row in markets:
        row["depth_verified"] = False
        if not args.start <= row["target_date"] <= args.end:
            continue
        clock = (pd.Timestamp(row["snapshot_time_utc"]) - pd.Timestamp(row["climate_day_start_utc"])).total_seconds() / 3600
        if args.all_afternoon or args.all_hours:
            start_hour, end_hour = args.all_hours or (14, 20)
            if start_hour <= clock < end_hour:
                key = (row["event_ticker"], row["snapshot_time_utc"])
                needed[key] = wx[key]["source_payload_ids"]
            continue
        if not 10 <= clock < 24:
            continue
        for side in ("yes", "no"):
            ask, bid = row.get(side + "_ask_dollars"), row.get(side + "_bid_dollars")
            if ask is not None and bid is not None and .5 <= ask <= .95 and 0 <= ask - bid <= .0500001:
                key = (row["event_ticker"], row["snapshot_time_utc"])
                needed[key] = wx[key]["source_payload_ids"]
    raw_ids = sorted({raw_id for payloads in needed.values() for raw_id in payloads.values() if raw_id})
    client = retry_reads(SupabaseClient.from_env())
    metadata = {}
    for offset in range(0, len(raw_ids), 80):
        group = raw_ids[offset:offset + 80]
        for item in client.select("raw_payloads", {"raw_payload_id": "in.(" + ",".join(group) + ")",
            "select": "raw_payload_id,storage_path,requested_at_utc,received_at_utc,success", "order": "raw_payload_id.asc"}):
            metadata[item["raw_payload_id"]] = item
    market_ids = sorted({r["kalshi_open_markets"] for r in needed.values()})
    print(f"Metadata: {len(metadata)}; market payloads needed: {len(market_ids)}", flush=True)

    def fetch(raw_id):
        cached = args.cache / f"{raw_id}.json.gz"
        if cached.exists():
            return raw_id, _load_json_gz(cached)
        item = metadata[raw_id]
        if not item["success"]:
            raise ValueError("Failed original market request")
        worker = retry_reads(SupabaseClient(client.config))
        payload = worker.download_json_gz(item["storage_path"])
        write_json_gz(cached, payload)
        return raw_id, payload

    payloads = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch, raw_id) for raw_id in market_ids]
        for index, future in enumerate(as_completed(futures), 1):
            raw_id, payload = future.result()
            payloads[raw_id] = {r["ticker"]: r for r in payload["markets"]}
            if index % 100 == 0:
                print(f"Raw payloads verified: {index}/{len(market_ids)}", flush=True)
    changed, verified = 0, 0
    for row in markets:
        key = (row["event_ticker"], row["snapshot_time_utc"])
        if key not in needed:
            continue
        source_ids = needed[key]
        raw_id = source_ids["kalshi_open_markets"]
        raw = payloads[raw_id].get(row["market_ticker"])
        if raw is None:
            raise ValueError("Saved market absent from its original payload")
        for side in ("yes", "no"):
            for leg in ("bid", "ask"):
                name = f"{side}_{leg}_size"
                corrected = quantity(raw, side, leg)
                changed += corrected != row.get(name)
                row[name] = corrected
        row["depth_verified"] = True
        row["market_received_at_utc"] = metadata[raw_id]["received_at_utc"]
        if args.all_hours:
            row["market_requested_at_utc"] = metadata[raw_id].get("requested_at_utc")
        times = [metadata[i]["received_at_utc"] for i in source_ids.values() if i in metadata]
        row["decision_available_at_utc"] = max(times, key=pd.Timestamp)
        row["availability_metadata_complete"] = all(i in metadata for i in source_ids.values())
        verified += 1
    for path in args.data.glob("*.json.gz"):
        write_json_gz(args.output / path.name, markets if path.name == "market_snapshots.json.gz" else _load_json_gz(path))
    if args.all_hours:
        write_json_gz(args.output / "source_receipts.json.gz", list(metadata.values()))
    import json
    manifest = json.loads((args.data / "manifest.json").read_text())
    audit = {"parent": str(args.data), "raw_payload_count": len(payloads),
             "verified_quote_rows": verified, "changed_size_fields": changed,
             "repair_start": args.start, "repair_end": args.end,
             "snapshot_selection": (f"all climate-hour [{args.all_hours[0]}, {args.all_hours[1]})" if args.all_hours
                                    else "all climate-hour 14-19" if args.all_afternoon
                                    else "quote-price filtered climate-hour 10-23"),
             "method": "original raw API contract counts, with NO ask size from YES bid size"}
    manifest["quote_reconstruction"] = audit
    write_json(args.output / "manifest.json", manifest)
    write_json(args.output / "quote_reconstruction_audit.json", audit)
    print(audit, flush=True)


if __name__ == "__main__":
    main()
