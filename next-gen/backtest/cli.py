"""Command line interface for the next-gen backtest engine."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from backtest.data_sources import LocalExportSource, SupabaseSource
from backtest.evaluate import evaluate_bracket_model
from backtest.export_supabase import (
    dataset_name,
    export_supabase,
    timestamped_export_dir,
    timestamped_report_dir,
    utc_filename_timestamp,
)
from backtest.health import (
    build_daily_health_report,
    daily_health_summary,
    write_daily_health_report,
)
from backtest.label_import import import_final_temperature_labels
from backtest.load_dataset import load_dataset
from backtest.pipeline import run_export_validate_pipeline
from backtest.quality import build_quality_report, write_quality_report
from backtest.reports import write_dataset_summary, write_result
from backtest.settlement_source_report import write_settlement_source_report
from backtest.validators import validate_dataset
from libs.config import load_dotenv
from libs.errors import SourceError
from libs.json_utils import write_json
from libs.settlement_policy import clamp_to_post_settlement_start
from libs.supabase_client import SupabaseClient


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Next-gen weather backtesting engine")
    commands = parser.add_subparsers(dest="command", required=True)

    export_parser = commands.add_parser("export", help="Export Supabase rows to local files.")
    export_parser.add_argument("--start", required=True)
    export_parser.add_argument("--end", required=True)
    export_parser.add_argument(
        "--output",
        type=Path,
        help="Output folder. Defaults to data/export_<start>_<end>_<UTC timestamp>.",
    )
    export_parser.add_argument(
        "--include-pre-settlement-system-data",
        action="store_true",
        help="Allow exporting dates before the 2026-08-27 settlement-system cutoff.",
    )

    validate_parser = commands.add_parser("validate", help="Validate a frozen local dataset.")
    validate_parser.add_argument("--data", type=Path, required=True)
    validate_parser.add_argument("--require-settlements", action="store_true")

    quality_parser = commands.add_parser("quality", help="Write data quality reports.")
    quality_parser.add_argument("--data", type=Path, required=True)
    quality_parser.add_argument(
        "--output",
        type=Path,
        help="Output folder. Defaults to reports/quality/data_quality_<dataset>_<UTC timestamp>.",
    )

    daily_health_parser = commands.add_parser(
        "daily-health",
        help="Write a day-specific collector health report from a local export or Supabase.",
    )
    daily_health_parser.add_argument("--date", required=True, help="Collection date YYYY-MM-DD.")
    daily_health_parser.add_argument("--data", type=Path, help="Frozen local export folder.")
    daily_health_parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Output folder. Defaults to "
            "reports/quality/daily_health_<date>_<source>_<UTC timestamp>."
        ),
    )

    source_report_parser = commands.add_parser(
        "settlement-sources",
        help="Write settlement-source coverage and NWS-vs-Weather Company label comparisons.",
    )
    source_report_parser.add_argument("--data", type=Path, required=True)
    source_report_parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Output folder. Defaults to "
            "reports/quality/settlement_sources_<dataset>_<UTC timestamp>."
        ),
    )

    import_labels_parser = commands.add_parser(
        "import-labels",
        help="Import alternate final-temperature labels into a local export.",
    )
    import_labels_parser.add_argument("--data", type=Path, required=True)
    import_labels_parser.add_argument("--labels", type=Path, required=True)
    import_labels_parser.add_argument(
        "--source-provider",
        required=True,
        choices=["nws_cli_daily", "weather_company_daily", "weather_company_hourly"],
        help="Official source for the imported final_high_f rows.",
    )

    monitor_parser = commands.add_parser(
        "monitor",
        help="Print live read-only Supabase collector health for a date.",
    )
    monitor_parser.add_argument(
        "--date",
        default=datetime.now(UTC).date().isoformat(),
        help="Collection date YYYY-MM-DD. Defaults to today's UTC date.",
    )

    pipeline_parser = commands.add_parser(
        "pipeline", help="Export Supabase data, validate it, and write quality reports."
    )
    pipeline_parser.add_argument("--start", required=True)
    pipeline_parser.add_argument("--end", required=True)
    pipeline_parser.add_argument(
        "--data-output",
        type=Path,
        help="Data output folder. Defaults to data/export_<start>_<end>_<UTC timestamp>.",
    )
    pipeline_parser.add_argument(
        "--report-output",
        type=Path,
        help=(
            "Report output folder. Defaults to "
            "reports/quality/pipeline_<start>_<end>_<UTC timestamp>."
        ),
    )
    pipeline_parser.add_argument("--require-settlements", action="store_true")
    pipeline_parser.add_argument(
        "--include-pre-settlement-system-data",
        action="store_true",
        help="Allow exporting dates before the 2026-08-27 settlement-system cutoff.",
    )

    evaluate_parser = commands.add_parser("evaluate", help="Evaluate stored model probabilities.")
    evaluate_parser.add_argument("--data", type=Path, required=True)
    evaluate_parser.add_argument("--model", required=True)
    evaluate_parser.add_argument(
        "--output",
        type=Path,
        help="Output folder. Defaults to reports/model/<model>_<dataset>_<UTC timestamp>.",
    )

    report_parser = commands.add_parser("report", help="Print a compact report summary.")
    report_parser.add_argument("--run", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "export":
        effective_start = (
            args.start
            if args.include_pre_settlement_system_data
            else clamp_to_post_settlement_start(args.start).isoformat()
        )
        output = args.output or timestamped_export_dir(effective_start, args.end)
        export_supabase(
            args.start,
            args.end,
            output,
            post_settlement_system_only=not args.include_pre_settlement_system_data,
        )
        print(f"export complete: {output}")
        return 0
    if args.command == "validate":
        dataset = load_dataset(LocalExportSource(args.data))
        validate_dataset(dataset, require_settlements=args.require_settlements)
        write_dataset_summary(dataset, args.data)
        return 0
    if args.command == "quality":
        source = LocalExportSource(args.data)
        report = build_quality_report(source, source_export_id=dataset_name(args.data))
        output = args.output or timestamped_report_dir(
            "quality", f"data_quality_{dataset_name(args.data)}"
        )
        write_quality_report(report, output)
        print(f"quality report complete: {output}")
        return 0
    if args.command == "daily-health":
        if args.data:
            source = LocalExportSource(args.data)
            source_id = dataset_name(args.data)
        else:
            source = SupabaseSource(SupabaseClient.from_env(), start=args.date, end=args.date)
            source_id = "supabase"
        report = build_daily_health_report(source, args.date, source_export_id=source_id)
        output = args.output or timestamped_report_dir(
            "quality",
            f"daily_health_{args.date}_{source_id}",
        )
        write_daily_health_report(report, output)
        print(daily_health_summary(report))
        print(f"daily health report complete: {output}")
        return 0
    if args.command == "settlement-sources":
        source = LocalExportSource(args.data)
        output = args.output or timestamped_report_dir(
            "quality",
            f"settlement_sources_{dataset_name(args.data)}",
        )
        result = write_settlement_source_report(
            source,
            output,
            source_export_id=dataset_name(args.data),
        )
        print(
            "settlement source report complete: "
            f"labels={result['label_rows']} comparable_pairs={result['nws_weather_company_pairs']} "
            f"output={output}"
        )
        return 0
    if args.command == "import-labels":
        result = import_final_temperature_labels(
            args.data,
            args.labels,
            source_provider=args.source_provider,
        )
        print(
            "labels imported: "
            f"source={result['source_provider']} imported={result['imported_rows']} "
            f"total={result['total_label_rows']} export={args.data}"
        )
        return 0
    if args.command == "monitor":
        client = SupabaseClient.from_env()
        try:
            health_rows = client.select("v_collector_health", {})
            if health_rows:
                row = health_rows[0]
                print(
                    "collector-health "
                    f"latest_run_utc={row.get('latest_run_utc')} "
                    f"cities_seen_last_2h={row.get('cities_seen_last_2h')} "
                    f"unsettled_events={row.get('unsettled_events')} "
                    f"provider_errors_24h={row.get('provider_errors_24h')} "
                    f"pending_final_highs={row.get('pending_final_highs')}"
                )
        except SourceError as exc:
            print(f"collector-health view unavailable: {exc}")
        source = SupabaseSource(client, start=args.date, end=args.date)
        report = build_daily_health_report(source, args.date, source_export_id="supabase")
        print(daily_health_summary(report))
        return 0
    if args.command == "pipeline":
        stamp = utc_filename_timestamp()
        effective_start = (
            args.start
            if args.include_pre_settlement_system_data
            else clamp_to_post_settlement_start(args.start).isoformat()
        )
        data_output = args.data_output or timestamped_export_dir(
            effective_start,
            args.end,
            stamp=stamp,
        )
        report_output = args.report_output or timestamped_report_dir(
            "quality",
            f"pipeline_{effective_start}_{args.end}",
            stamp=stamp,
        )
        result = run_export_validate_pipeline(
            args.start,
            args.end,
            data_output,
            report_output,
            require_settlements=args.require_settlements,
            post_settlement_system_only=not args.include_pre_settlement_system_data,
        )
        print(f"pipeline complete: data={result.data_dir} report={result.report_dir}")
        return 0
    if args.command == "evaluate":
        dataset = load_dataset(LocalExportSource(args.data))
        validate_dataset(dataset)
        result = evaluate_bracket_model(
            dataset,
            args.model,
            source_export_id=dataset_name(args.data),
        )
        output = args.output or timestamped_report_dir(
            "model", f"{args.model}_{dataset_name(args.data)}"
        )
        write_dataset_summary(dataset, output)
        write_result(result, output)
        print(f"evaluation report complete: {output}")
        return 0
    if args.command == "report":
        import json

        summary_path = args.run / "dataset_summary.json"
        if summary_path.exists():
            print(json.dumps(__import__("json").loads(summary_path.read_text()), indent=2))
        else:
            write_json(args.run / "dataset_summary.json", {})
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
