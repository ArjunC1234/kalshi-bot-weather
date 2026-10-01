"""Run registry-defined commands as local tracked jobs."""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import subprocess
import sys
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from control.jobs.store import JobRecord, JobStore
from control.registry.loader import Registry, RegistryEntry

CONTROL_PARAM_KEYS = {
    "dataset_path",
    "model_artifact_path",
    "model_report_path",
    "output_path",
    "timeout_seconds",
}


class JobRunner:
    def __init__(self, store: JobStore, repo_root: Path = Path(".")) -> None:
        self.store = store
        self.repo_root = repo_root.resolve()
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def create_registered_job(
        self,
        registry: Registry,
        kind: str,
        registry_id: str,
        entrypoint: str,
        params: dict[str, Any],
    ) -> JobRecord:
        entry = registry.get(kind, registry_id)
        if entry is None:
            raise ValueError(f"registry entry not found: {kind}/{registry_id}")
        command, cwd, output_path = self._materialize_command(entry, entrypoint, params)
        manifest = self._run_manifest(entry, entrypoint, output_path, params)
        record = self.store.create(kind, registry_id, entrypoint, params, command, cwd, output_path)
        thread = threading.Thread(target=self._run, args=(record, manifest), daemon=True)
        thread.start()
        return record

    def create_command_job(
        self,
        kind: str,
        registry_id: str,
        entrypoint: str,
        params: dict[str, Any],
        command: list[str],
        cwd: Path | None = None,
        output_path: Path | None = None,
    ) -> JobRecord:
        record = self.store.create(kind, registry_id, entrypoint, params, command, cwd, output_path)
        thread = threading.Thread(target=self._run, args=(record,), daemon=True)
        thread.start()
        return record

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            process = self._processes.get(job_id)
        if process is not None and process.poll() is None:
            process.terminate()
            self.store.update(job_id, "cancelled")
            return True
        job = self.store.get(job_id)
        if job is not None and job.status in {"queued", "running"}:
            self.store.update(job_id, "cancelled")
            return True
        return False

    def _materialize_command(
        self,
        entry: RegistryEntry,
        entrypoint: str,
        params: dict[str, Any],
    ) -> tuple[list[str], Path | None, Path | None]:
        entrypoints = entry.spec.get("entrypoints", {})
        config = entrypoints.get(entrypoint)
        if not isinstance(config, dict):
            raise ValueError(f"entrypoint not found: {entrypoint}")
        command_template = config.get("command")
        if not isinstance(command_template, list):
            raise ValueError("entrypoint command is invalid")
        cwd = self._resolve_cwd(entry)
        output_path = self._output_path(entry, entrypoint, params)
        context = {
            "inputs.dataset.path": str(params.get("dataset_path", "")),
            "inputs.model_artifact.path": str(params.get("model_artifact_path", "")),
            "inputs.model_report.path": str(params.get("model_report_path", "")),
            "outputs.artifact_dir": str(output_path) if output_path else "",
            "outputs.report_dir": str(output_path) if output_path else "",
        }
        command = [_replace_tokens(part, context) for part in command_template]
        if command and command[0] == "python":
            command[0] = sys.executable
        allowed_params = _entrypoint_param_names(config)
        for key, value in params.items():
            if key in CONTROL_PARAM_KEYS:
                continue
            if key not in allowed_params:
                raise ValueError(f"unregistered entrypoint parameter: {key}")
            if isinstance(value, bool):
                prefix = "--" if value else "--no-"
                command.append(f"{prefix}{key.replace('_', '-')}")
            else:
                command.extend([f"--{key.replace('_', '-')}", str(value)])
        return command, cwd, output_path

    def _resolve_cwd(self, entry: RegistryEntry) -> Path | None:
        value = entry.spec.get("code", {}).get("working_directory")
        if not value:
            return self.repo_root
        candidate = (self.repo_root / str(value)).resolve()
        if not _is_relative_to(candidate, self.repo_root):
            raise ValueError("registry working_directory escapes repository root")
        return candidate

    def _output_path(
        self,
        entry: RegistryEntry,
        entrypoint: str,
        params: dict[str, Any],
    ) -> Path | None:
        config = entry.spec.get("entrypoints", {}).get(entrypoint, {})
        artifact_type = config.get("produces", {}).get("artifact_type")
        if params.get("output_path"):
            candidate = (self.repo_root / str(params["output_path"])).resolve()
        else:
            root = _default_output_root(artifact_type)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            name = _generated_output_name(
                entry,
                entrypoint,
                config,
                params,
                stamp,
                self.repo_root,
            )
            candidate = (self.repo_root / root / name).resolve()
        if not _is_relative_to(candidate, self.repo_root):
            raise ValueError("job output_path escapes repository root")
        if artifact_type == "strategy_report" and _is_relative_to(candidate, self.repo_root / "reports" / "model"):
            raise ValueError("strategy report output_path must be under reports/strategy, not reports/model")
        if artifact_type == "model_report" and _is_relative_to(candidate, self.repo_root / "reports" / "strategy"):
            raise ValueError("model report output_path must be under reports/model, not reports/strategy")
        if artifact_type == "model_artifact" and _is_relative_to(candidate, self.repo_root / "reports"):
            raise ValueError("model artifact output_path must be under models, not reports")
        return candidate

    def _run_manifest(
        self,
        entry: RegistryEntry,
        entrypoint: str,
        output_path: Path | None,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        if output_path is None:
            return None
        produces = (
            entry.spec.get("entrypoints", {})
            .get(entrypoint, {})
            .get("produces", {})
        )
        if not isinstance(produces, dict):
            produces = {}
        return {
            "artifact_type": produces.get("artifact_type"),
            "contract": produces.get("contract"),
            "registry_id": entry.id,
            "registry_kind": entry.kind,
            "entrypoint": entrypoint,
            "source_export_id": Path(str(params.get("dataset_path", ""))).name or None,
            "settlement_sources": _dataset_settlement_sources(params.get("dataset_path"), self.repo_root),
            "created_utc": datetime.now(UTC).isoformat(),
        }

    def _run(self, record: JobRecord, manifest: dict[str, Any] | None = None) -> None:
        self.store.update(record.id, "running")
        log_path = Path(record.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            log.write(f"$ {' '.join(record.command)}\n")
            log.flush()
            process: subprocess.Popen | None = None
            try:
                creationflags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
                    else 0
                )
                process = subprocess.Popen(
                    record.command,
                    cwd=record.cwd or None,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                    creationflags=creationflags,
                )
                with self._lock:
                    self._processes[record.id] = process
                returncode = process.wait(timeout=_timeout(record.params))
            except subprocess.TimeoutExpired:
                if process is not None:
                    process.kill()
                self.store.update(record.id, "failed", error="job timed out")
                log.write("\njob timed out and was killed\n")
                self._write_run_manifest(record, manifest, "failed", error="job timed out")
                return
            except Exception as exc:  # pragma: no cover - process boundary.
                self.store.update(record.id, "failed", error=str(exc))
                log.write(f"\njob failed before process completion: {exc}\n")
                self._write_run_manifest(record, manifest, "failed", error=str(exc))
                return
            finally:
                with self._lock:
                    self._processes.pop(record.id, None)
        current = self.store.get(record.id)
        cancelled = current is not None and current.status == "cancelled"
        status = "cancelled" if cancelled else "succeeded" if returncode == 0 else "failed"
        manifest_status = "cancelled" if cancelled else "complete" if returncode == 0 else "failed"
        self._write_run_manifest(record, manifest, manifest_status, returncode=returncode)
        self.store.update(
            record.id,
            status,
            returncode=returncode,
        )

    def _write_run_manifest(
        self,
        record: JobRecord,
        manifest: dict[str, Any] | None,
        status: str,
        returncode: int | None = None,
        error: str | None = None,
    ) -> None:
        if manifest is None or record.output_path is None:
            return
        output = Path(record.output_path)
        if not _is_relative_to(output.resolve(), self.repo_root):
            return
        output.mkdir(parents=True, exist_ok=True)
        existing_path = output / "run_manifest.json"
        existing: dict[str, Any] = {}
        if existing_path.exists():
            try:
                value = json.loads(existing_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                value = {}
            if isinstance(value, dict):
                existing = value
        payload = {
            **manifest,
            **existing,
            "job_id": record.id,
            "status": status,
            "updated_utc": datetime.now(UTC).isoformat(),
            "returncode": returncode,
        }
        if error:
            payload["error"] = error
        existing_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _replace_tokens(value: str, context: dict[str, str]) -> str:
    output = value
    for key, replacement in context.items():
        output = output.replace("{{ " + key + " }}", replacement)
        output = output.replace("{{" + key + "}}", replacement)
    return output


def _entrypoint_param_names(config: dict[str, Any]) -> set[str]:
    params_schema = config.get("params_schema")
    if not isinstance(params_schema, dict):
        return set()
    properties = params_schema.get("properties")
    if not isinstance(properties, dict):
        return set()
    return {str(key) for key in properties}


def _default_output_root(artifact_type: str | None) -> str:
    if artifact_type == "strategy_report":
        return "reports/strategy"
    if artifact_type == "quality_report":
        return "reports/quality"
    if artifact_type == "model_artifact":
        return "models"
    return "reports/model"


def _generated_output_name(
    entry: RegistryEntry,
    entrypoint: str,
    config: dict[str, Any],
    params: dict[str, Any],
    stamp: str,
    repo_root: Path,
) -> str:
    parts = [_path_token(entry.id), _path_token(entrypoint)]
    mode = _param_with_default(params, config, "mode")
    if mode not in (None, ""):
        parts.append(_path_token(mode))
    train_days = _param_with_default(params, config, "train_days")
    test_days = _param_with_default(params, config, "test_days")
    if train_days not in (None, ""):
        parts.append(f"{_path_token(train_days)}DTRAIN")
    if test_days not in (None, ""):
        parts.append(f"{_path_token(test_days)}DTEST")
    test_range = _test_date_range(entrypoint, config, params, repo_root)
    if test_range is not None:
        parts.extend(["TEST", test_range[0].strftime("%Y%m%d"), test_range[1].strftime("%Y%m%d")])
    else:
        source = Path(str(params.get("dataset_path", "dataset"))).name
        parts.append(_path_token(source))
    parts.extend(["CREATED", stamp])
    return "_".join(part for part in parts if part)


def _test_date_range(
    entrypoint: str,
    config: dict[str, Any],
    params: dict[str, Any],
    repo_root: Path,
) -> tuple[date, date] | None:
    fixed_start = _parse_date_param(params.get("test_start"))
    fixed_end = _parse_date_param(params.get("test_end"))
    if fixed_start and fixed_end:
        return fixed_start, fixed_end
    if entrypoint not in {"rolling_eval", "rolling"}:
        return None
    train_days = _positive_int(_param_with_default(params, config, "train_days"))
    if train_days is None:
        return None
    target_dates = _dataset_target_dates(params.get("dataset_path"), repo_root)
    if len(target_dates) <= train_days:
        return None
    return target_dates[train_days], target_dates[-1]


def _param_with_default(params: dict[str, Any], config: dict[str, Any], key: str) -> Any:
    if key in params and params[key] not in (None, ""):
        return params[key]
    properties = config.get("params_schema", {}).get("properties", {})
    if isinstance(properties, dict):
        spec = properties.get(key, {})
        if isinstance(spec, dict) and "default" in spec:
            return spec["default"]
    return None


def _dataset_target_dates(dataset_path: Any, repo_root: Path) -> list[date]:
    if not dataset_path:
        return []
    root = Path(str(dataset_path))
    if not root.is_absolute():
        root = repo_root / root
    candidates = [
        root / "events.csv",
        root / "events.json",
        root / "events.json.gz",
        root / "market_snapshots.csv",
        root / "market_snapshots.json",
        root / "market_snapshots.json.gz",
        root / "weather_snapshots.csv",
        root / "weather_snapshots.json",
        root / "weather_snapshots.json.gz",
        root / "final_temperature_labels.csv",
        root / "final_temperature_labels.json",
        root / "final_temperature_labels.json.gz",
    ]
    values: set[date] = set()
    for path in candidates:
        if not path.exists():
            continue
        for row in _read_export_rows(path):
            value = _parse_date_param(row.get("target_date") or row.get("snapshot_local_date"))
            if value is not None:
                values.add(value)
        if values:
            break
    return sorted(values)


def _dataset_settlement_sources(dataset_path: Any, repo_root: Path) -> dict[str, Any]:
    if not dataset_path:
        return {}
    root = Path(str(dataset_path))
    if not root.is_absolute():
        root = repo_root / root
    manifest = root / "manifest.json"
    if not manifest.exists():
        return {}
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    metadata = value.get("settlement_sources") if isinstance(value, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def _read_export_rows(path: Path) -> list[dict[str, Any]]:
    try:
        if path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(handle))
        if path.name.endswith(".json.gz"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                value = json.load(handle)
        else:
            value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _parse_date_param(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _path_token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip()).strip("_")
    return token.upper()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _timeout(params: dict[str, Any]) -> float | None:
    value = params.get("timeout_seconds")
    if value in (None, ""):
        return None
    return max(float(value), 1.0)
