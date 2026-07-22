"""Run registry-defined commands as local tracked jobs."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control.jobs.store import JobRecord, JobStore
from control.registry.loader import Registry, RegistryEntry


class JobRunner:
    def __init__(self, store: JobStore, repo_root: Path = Path(".")) -> None:
        self.store = store
        self.repo_root = repo_root.resolve()

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
        record = self.store.create(kind, registry_id, entrypoint, params, command, cwd, output_path)
        thread = threading.Thread(target=self._run, args=(record,), daemon=True)
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
        for key, value in params.items():
            if key in {"dataset_path", "model_report_path", "output_path"}:
                continue
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

    def _run(self, record: JobRecord) -> None:
        self.store.update(record.id, "running")
        log_path = Path(record.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            log.write(f"$ {' '.join(record.command)}\n")
            log.flush()
            try:
                process = subprocess.Popen(
                    record.command,
                    cwd=record.cwd or None,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                )
                returncode = process.wait()
            except Exception as exc:  # pragma: no cover - process boundary.
                self.store.update(record.id, "failed", error=str(exc))
                log.write(f"\njob failed before process completion: {exc}\n")
                return
        self.store.update(
            record.id,
            "succeeded" if returncode == 0 else "failed",
            returncode=returncode,
        )


def _replace_tokens(value: str, context: dict[str, str]) -> str:
    output = value
    for key, replacement in context.items():
        output = output.replace("{{ " + key + " }}", replacement)
        output = output.replace("{{" + key + "}}", replacement)
    return output


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
