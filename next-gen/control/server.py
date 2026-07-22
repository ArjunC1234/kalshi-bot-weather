"""HTTP server for the Kalshi Bot Control Center backend API."""

from __future__ import annotations

import json
import socketserver
import sys
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from control.artifacts.index import ArtifactIndex, ArtifactRecord, scan_artifacts
from control.bot import bot_stub
from control.compatibility import check_model_dataset_compatibility
from control.dashboard import dashboard_summary
from control.exports.manager import (
    archive_export,
    clone_export,
    compare_exports,
    extend_export,
    inspect_export,
    list_exports,
    reduce_export,
    validate_export,
)
from control.jobs.runner import JobRunner
from control.jobs.store import JobStore, serialize_job
from control.providers.supabase_export import preview_export_profile
from control.registry.loader import Registry, load_registry
from control.visualizations.query import execute_visualization_query


def serve_control(
    host: str = "127.0.0.1",
    port: int = 8775,
    registry_root: Path | None = None,
    repo_root: Path = Path("."),
) -> int:
    active_root = repo_root.resolve()
    registry = load_registry(registry_root)
    artifact_index = ArtifactIndex(active_root / ".control/artifacts.sqlite")
    job_store = JobStore(active_root / ".control/jobs/jobs.sqlite")
    runner = JobRunner(job_store, active_root)

    class ControlHandler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib API name.
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/control/api/registry":
                    self._send_json(registry.as_dict())
                    return
                if parsed.path == "/control/api/dashboard":
                    self._send_json(dashboard_summary(active_root, registry, job_store))
                    return
                if parsed.path == "/control/api/export-profiles":
                    self._send_json(
                        {
                            "export_profiles": [
                                entry.spec for entry in registry.by_kind("export_profile")
                            ]
                        }
                    )
                    return
                if parsed.path == "/control/api/models":
                    self._send_json(
                        {
                            "models": [entry.spec for entry in registry.by_kind("model")],
                            "strategies": [entry.spec for entry in registry.by_kind("strategy")],
                        }
                    )
                    return
                if parsed.path == "/control/api/artifacts":
                    records = _records(active_root)
                    artifact_index.replace_all(records)
                    self._send_json({"artifacts": artifact_index.list()})
                    return
                if parsed.path == "/control/api/exports":
                    self._send_json({"exports": list_exports(active_root / "data")})
                    return
                if parsed.path.startswith("/control/api/exports/"):
                    export_id = unquote(parsed.path.removeprefix("/control/api/exports/"))
                    export_path = _artifact_path(active_root, "local_export", export_id)
                    self._send_json(inspect_export(export_path))
                    return
                if parsed.path == "/control/api/reports":
                    self._send_json(
                        {
                            "reports": [
                                record.metadata
                                for record in _records(active_root)
                                if record.artifact_type
                                in {"model_report", "strategy_report", "quality_report"}
                            ]
                        }
                    )
                    return
                if parsed.path == "/control/api/datasets":
                    self._send_json({"datasets": list_exports(active_root / "data")})
                    return
                if parsed.path.startswith("/control/api/datasets/"):
                    self._handle_dataset_get(parsed.path)
                    return
                if parsed.path == "/control/api/jobs":
                    self._send_json({"jobs": [serialize_job(job) for job in job_store.list()]})
                    return
                if parsed.path.startswith("/control/api/jobs/"):
                    self._handle_job_get(parsed)
                    return
                if parsed.path.startswith("/control/api/bot/"):
                    resource = unquote(parsed.path.removeprefix("/control/api/bot/"))
                    self._send_json(bot_stub(resource))
                    return
            except ValueError as exc:
                self._send_error_json(400, str(exc))
                return
            except Exception as exc:  # pragma: no cover - HTTP safety boundary.
                self._send_error_json(500, str(exc))
                return
            self._send_error_json(404, "not found")

        def do_POST(self) -> None:  # noqa: N802 - stdlib API name.
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/control/api/jobs":
                    request = self._read_json()
                    job = runner.create_registered_job(
                        registry,
                        str(request.get("kind", "model")),
                        str(request.get("registry_id", "")),
                        str(request.get("entrypoint", "evaluate")),
                        _dict(request.get("params", {})),
                    )
                    self._send_json(serialize_job(job), status=202)
                    return
                if parsed.path.startswith("/control/api/jobs/") and parsed.path.endswith("/cancel"):
                    job_id = unquote(
                        parsed.path.removeprefix("/control/api/jobs/")
                        .removesuffix("/cancel")
                        .strip("/")
                    )
                    self._send_json({"job_id": job_id, "cancelled": runner.cancel(job_id)})
                    return
                if parsed.path == "/control/api/exports/preview":
                    request = self._read_json()
                    profile = _required_entry(
                        registry,
                        "export_profile",
                        str(request.get("profile_id", "")),
                    )
                    self._send_json(preview_export_profile(profile))
                    return
                if parsed.path == "/control/api/exports/create":
                    self._handle_export_create()
                    return
                if parsed.path == "/control/api/exports/validate":
                    request = self._read_json()
                    export_path = _artifact_path(
                        active_root,
                        "local_export",
                        str(request.get("export_id", "")),
                    )
                    self._send_json(validate_export(export_path))
                    return
                if parsed.path == "/control/api/exports/compare":
                    request = self._read_json()
                    left = _artifact_path(
                        active_root,
                        "local_export",
                        str(request.get("left_export_id", "")),
                    )
                    right = _artifact_path(
                        active_root,
                        "local_export",
                        str(request.get("right_export_id", "")),
                    )
                    self._send_json(compare_exports(left, right))
                    return
                if parsed.path == "/control/api/exports/clone":
                    request = self._read_json()
                    source = _artifact_path(
                        active_root,
                        "local_export",
                        str(request.get("export_id", "")),
                    )
                    destination = _safe_path(active_root, str(request.get("destination", "")))
                    self._send_json(clone_export(source, destination))
                    return
                if parsed.path == "/control/api/exports/reduce":
                    request = self._read_json()
                    source = _artifact_path(
                        active_root,
                        "local_export",
                        str(request.get("export_id", "")),
                    )
                    destination = _safe_path(active_root, str(request.get("destination", "")))
                    self._send_json(
                        reduce_export(
                            source,
                            destination,
                            tables=_list_or_none(request.get("tables")),
                            cities=_list_or_none(request.get("cities")),
                            start=request.get("start"),
                            end=request.get("end"),
                        )
                    )
                    return
                if parsed.path == "/control/api/exports/extend":
                    request = self._read_json()
                    source = _artifact_path(
                        active_root,
                        "local_export",
                        str(request.get("export_id", "")),
                    )
                    extension = _artifact_path(
                        active_root,
                        "local_export",
                        str(request.get("extension_export_id", "")),
                    )
                    destination = _safe_path(active_root, str(request.get("destination", "")))
                    self._send_json(extend_export(source, extension, destination))
                    return
                if parsed.path == "/control/api/exports/archive":
                    request = self._read_json()
                    source = _artifact_path(
                        active_root,
                        "local_export",
                        str(request.get("export_id", "")),
                    )
                    self._send_json(archive_export(source, active_root / "data/.archive"))
                    return
                if parsed.path == "/control/api/compatibility/model-run":
                    request = self._read_json()
                    model = _required_entry(registry, "model", str(request.get("model_id", "")))
                    dataset_path = _safe_path(active_root, str(request.get("dataset_path", "")))
                    self._send_json(check_model_dataset_compatibility(model, dataset_path))
                    return
                if parsed.path == "/control/api/visualizations/query":
                    request = self._read_json()
                    artifact_path = _safe_path(active_root, str(request.get("artifact_path", "")))
                    self._send_json(
                        execute_visualization_query(
                            artifact_path,
                            _dict(request.get("query", request)),
                        )
                    )
                    return
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_error_json(400, str(exc))
                return
            except Exception as exc:  # pragma: no cover - HTTP safety boundary.
                self._send_error_json(500, str(exc))
                return
            self._send_error_json(404, "not found")

        def _handle_export_create(self) -> None:
            request = self._read_json()
            profile_id = str(request.get("profile_id", ""))
            start = str(request.get("start", ""))
            end = str(request.get("end", ""))
            if not profile_id or not start or not end:
                raise ValueError("profile_id, start, and end are required")
            _required_entry(registry, "export_profile", profile_id)
            output_path = request.get("output_path") or (
                f"data/export_{_path_token(start)}_{_path_token(end)}_control"
            )
            output_resolved = _safe_path(active_root, str(output_path))
            command = [
                sys.executable,
                "-m",
                "control.cli",
                "export",
                "--profile",
                profile_id,
                "--start",
                start,
                "--end",
                end,
                "--output",
                str(output_resolved),
            ]
            job = runner.create_command_job(
                "export_profile",
                profile_id,
                "create",
                {
                    "profile_id": profile_id,
                    "start": start,
                    "end": end,
                    "output_path": str(output_resolved),
                    "artifact_type": "local_export",
                },
                command,
                cwd=active_root,
                output_path=output_resolved,
            )
            self._send_json(serialize_job(job), status=202)

        def _handle_dataset_get(self, path: str) -> None:
            parts = [
                unquote(part)
                for part in path.removeprefix("/control/api/datasets/").split("/")
                if part
            ]
            if not parts:
                raise ValueError("dataset id is required")
            dataset_path = _artifact_path(active_root, "local_export", parts[0])
            inspected = inspect_export(dataset_path)
            if len(parts) == 1:
                self._send_json(inspected)
                return
            if parts[1] == "coverage":
                self._send_json(inspected.get("coverage", {}))
                return
            if parts[1] == "cities":
                cities = inspected.get("coverage", {}).get("cities", [])
                if len(parts) == 2:
                    self._send_json({"dataset_id": parts[0], "cities": cities})
                    return
                city = parts[2]
                self._send_json(
                    {
                        "dataset_id": parts[0],
                        "city": city,
                        "available": city in cities,
                        "coverage": inspected.get("coverage", {}),
                    }
                )
                return
            raise ValueError(f"unknown dataset resource: {parts[1]}")

        def _handle_job_get(self, parsed) -> None:
            remainder = unquote(parsed.path.removeprefix("/control/api/jobs/"))
            if remainder.endswith("/logs"):
                job_id = remainder.removesuffix("/logs").rstrip("/")
                job = job_store.get(job_id)
                if job is None:
                    self._send_error_json(404, "job not found")
                    return
                query = parse_qs(parsed.query)
                lines = min(max(int(query.get("lines", ["400"])[0]), 1), 5000)
                self._send_json({"job_id": job_id, "log": _tail_text(Path(job.log_path), lines)})
                return
            job = job_store.get(remainder)
            if job is None:
                self._send_error_json(404, "job not found")
                return
            self._send_json(serialize_job(job))

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("request body must be a JSON object")
            return value

        def _send_json(self, value: object, status: int = 200) -> None:
            body = json.dumps(value, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json({"error": message}, status=status)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib name.
            return

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    with ReusableTCPServer((host, port), ControlHandler) as server:
        print(f"Kalshi Bot Control Center API: http://{host}:{server.server_address[1]}/control/api")
        server.serve_forever()
    return 0


def _required_entry(registry: Registry, kind: str, entry_id: str):
    entry = registry.get(kind, entry_id)
    if entry is None:
        raise ValueError(f"registry entry not found: {kind}/{entry_id}")
    return entry


def _records(active_root: Path) -> list[ArtifactRecord]:
    return scan_artifacts(
        active_root / "data",
        active_root / "reports/model",
        active_root / "reports/quality",
        active_root / "reports/strategy",
    )


def _artifact_path(active_root: Path, artifact_type: str, artifact_id: str) -> Path:
    if not artifact_id:
        raise ValueError("artifact id is required")
    for record in _records(active_root):
        if record.artifact_type == artifact_type and record.id == artifact_id:
            return _safe_path(active_root, record.path)
    raise ValueError(f"{artifact_type} not found: {artifact_id}")


def _safe_path(active_root: Path, value: str) -> Path:
    if not value:
        raise ValueError("path is required")
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (active_root / candidate).resolve()
    if not _is_relative_to(resolved, active_root):
        raise ValueError("path escapes repository root")
    return resolved


def _tail_text(path: Path, lines: int) -> str:
    if not path.exists():
        return ""
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("params must be an object")
    return value


def _list_or_none(value: Any) -> list[str] | None:
    return [str(item) for item in value] if isinstance(value, list) else None


def _path_token(value: str) -> str:
    return "".join(char if char.isalnum() else "" for char in value)[:48] or "range"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
