"""Read-only verification of timing spool receipts and their committed database rows."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/kalshi-weather-next-gen"))
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--min-runs", type=int, default=2)
    args = parser.parse_args()
    sys.path.insert(0, str(args.root / "collector"))
    from config import load_dotenv, settings_from_env
    from spool import read_json_gz

    data_dir = args.data_dir or args.root / "collector_spool_v3" / "timing"
    folder = "pending" if args.local_only else "synced"
    paths = sorted((data_dir / folder).glob("*.json.gz"))[-args.min_runs :]
    if len(paths) < args.min_runs:
        raise RuntimeError(f"expected {args.min_runs} {folder} captures, found {len(paths)}")
    reports, all_ids = [], set()
    connection = None
    if not args.local_only:
        import psycopg

        load_dotenv(args.root / ".env")
        connection = psycopg.connect(settings_from_env().database_url, connect_timeout=10)
    try:
        for path in paths:
            payload = read_json_gz(path)
            run = payload["tables"]["collector_runs"][0]
            raw = payload["raw_payloads"]
            ids = {record["raw_payload_id"] for record in raw}
            assert len(ids) == len(raw) and not (ids & all_ids), "receipt ID collision"
            all_ids.update(ids)
            assert run["city_count_completed"] == run["city_count_attempted"] == 6, run
            assert run["provider_error_count"] == 0, payload["tables"]["provider_errors"]
            assert len(raw) == 24, len(raw)
            delays = []
            for city in run["metadata"]["cities"]:
                assert city["market_count"] == 6 and city["post_forecast_order_verified"], city
                ready = datetime.fromisoformat(city["forecast_ready_at_utc"])
                quote = datetime.fromisoformat(city["quote_requested_at_utc"])
                delays.append((quote - ready).total_seconds())
                assert min(delays) >= 0
            db_raw = None
            if connection is not None:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "select count(*) from collector_runs where collector_run_id = %s",
                        (run["collector_run_id"],),
                    )
                    assert cursor.fetchone()[0] == 1, "run not committed"
                    cursor.execute(
                        "select raw_payload_id, requested_at_utc, received_at_utc "
                        "from raw_payloads where collector_run_id = %s",
                        (run["collector_run_id"],),
                    )
                    stored = cursor.fetchall()
                    assert {row[0] for row in stored} == ids, "database receipts differ from spool"
                    by_id = {record["raw_payload_id"]: record for record in raw}
                    for raw_id, requested, received in stored:
                        assert requested == datetime.fromisoformat(
                            by_id[raw_id]["requested_at_utc"]
                        )
                        assert received == datetime.fromisoformat(by_id[raw_id]["received_at_utc"])
                    db_raw = len(stored)
            reports.append(
                {
                    "run_id": run["collector_run_id"],
                    "snapshot": run["snapshot_time_utc"],
                    "cities_completed": run["city_count_completed"],
                    "raw_receipts": len(raw),
                    "database_receipts": db_raw,
                    "errors": run["provider_error_count"],
                    "max_forecast_to_quote_request_seconds": max(delays),
                    "spool_bytes": path.stat().st_size,
                }
            )
    finally:
        if connection is not None:
            connection.close()
    print(
        json.dumps(
            {
                "verified": True,
                "captures": reports,
                "pending_files": len(list((data_dir / "pending").glob("*.json.gz"))),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
