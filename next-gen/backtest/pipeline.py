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


@dataclass(frozen=True)
class PipelineResult:
    data_dir: str
    report_dir: str
    exported_at_utc: str
    start: str
    end: str
    require_settlements: bool
    validation_status: str


def run_export_validate_pipeline(
    start: str,
    end: str,
    data_dir: Path,
    report_dir: Path,
    require_settlements: bool = False,
) -> PipelineResult:
    export_supabase(start, end, data_dir)
    source = LocalExportSource(data_dir)
    dataset = load_dataset(source)
    validate_dataset(dataset, require_settlements=require_settlements)
    write_dataset_summary(dataset, data_dir)
    report = build_quality_report(source)
    write_quality_report(report, report_dir)
    result = PipelineResult(
        data_dir=str(data_dir),
        report_dir=str(report_dir),
        exported_at_utc=datetime.now(UTC).isoformat(),
        start=start,
        end=end,
        require_settlements=require_settlements,
        validation_status="valid",
    )
    write_json(report_dir / "pipeline_result.json", asdict(result))
    return result

