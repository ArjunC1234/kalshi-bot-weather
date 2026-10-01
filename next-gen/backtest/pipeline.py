"""One-command export, validation, and quality-report pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from backtest.data_sources import LocalExportSource
from backtest.export_supabase import export_supabase
from backtest.load_dataset import load_dataset
from backtest.quality import build_quality_report, write_quality_report
from backtest.reports import write_dataset_summary
from backtest.validators import validate_dataset
from libs.json_utils import write_json
from libs.settlement_policy import clamp_to_post_settlement_start


@dataclass(frozen=True)
class PipelineResult:
    data_dir: str
    report_dir: str
    exported_at_utc: str
    start: str
    requested_start: str
    end: str
    require_settlements: bool
    post_settlement_system_only: bool
    validation_status: str


def run_export_validate_pipeline(
    start: str,
    end: str,
    data_dir: Path,
    report_dir: Path,
    require_settlements: bool = False,
    post_settlement_system_only: bool = True,
) -> PipelineResult:
    requested_start = start
    effective_start = (
        clamp_to_post_settlement_start(start).isoformat()
        if post_settlement_system_only
        else start
    )
    export_supabase(
        start,
        end,
        data_dir,
        post_settlement_system_only=post_settlement_system_only,
    )
    source = LocalExportSource(data_dir)
    dataset = load_dataset(source)
    validate_dataset(dataset, require_settlements=require_settlements)
    write_dataset_summary(dataset, data_dir)
    report = build_quality_report(source, source_export_id=data_dir.name)
    write_quality_report(report, report_dir)
    result = PipelineResult(
        data_dir=str(data_dir),
        report_dir=str(report_dir),
        exported_at_utc=datetime.now(UTC).isoformat(),
        start=effective_start,
        requested_start=requested_start,
        end=end,
        require_settlements=require_settlements,
        post_settlement_system_only=post_settlement_system_only,
        validation_status="valid",
    )
    write_json(report_dir / "pipeline_result.json", asdict(result))
    return result
