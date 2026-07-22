"""Run registry-defined commands as local tracked jobs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control.jobs.store import JobRecord, JobStore
from control.registry.loader import Registry, RegistryEntry

CONTROL_PARAM_KEYS = {
    "dataset_path",
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
            "inputs.model_report.path": str(params.get("model_report_path", "")),
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
                if value:
                    command.append(f"--{key.replace('_', '-')}")
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
        if params.get("output_path"):
            candidate = (self.repo_root / str(params["output_path"])).resolve()
        else:
            artifact_type = (
                entry.spec.get("entrypoints", {})
                .get(entrypoint, {})
                .get("produces", {})
                .get("artifact_type")
            )
            root = "reports/strategy" if artifact_type == "strategy_report" else "reports/model"
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            source = Path(str(params.get("dataset_path", "dataset"))).name
            candidate = (self.repo_root / root / f"{entry.id}_{source}_{stamp}").resolve()
        if not _is_relative_to(candidate, self.repo_root):
            raise ValueError("job output_path escapes repository root")
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
