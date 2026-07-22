"""HTTP server for the control-plane API.

Routes are mounted under /control/api/* so existing Trends /api/* behavior can
stay unchanged during migration.
"""

from __future__ import annotations

import json
import socketserver
import sys
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from control.artifacts.index import ArtifactIndex, scan_artifacts
from control.compatibility import check_model_dataset_compatibility
from control.jobs.runner import JobRunner
from control.jobs.store import JobStore, serialize_job
from control.providers.supabase_export import preview_export_profile
from control.registry.loader import Registry, load_registry


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
                if parsed.path == "/control/api/artifacts":
                    records = scan_artifacts(
                        active_root / "data",
                        active_root / "reports/model",
                        active_root / "reports/quality",
                        active_root / "reports/strategy",
                    )
                    artifact_index.replace_all(records)
                    self._send_json({"artifacts": artifact_index.list()})
                    return
                if parsed.path == "/control/api/jobs":
                    self._send_json({"jobs": [serialize_job(job) for job in job_store.list()]})
                    return
                if parsed.path.startswith("/control/api/jobs/"):
                    remainder = unquote(parsed.path.removeprefix("/control/api/jobs/"))
                    if remainder.endswith("/logs"):
                        job_id = remainder.removesuffix("/logs").rstrip("/")
                        job = job_store.get(job_id)
                        if job is None:
                            self._send_error_json(404, "job not found")
                            return
                        log_path = Path(job.log_path)
                        log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                        self._send_json({"job_id": job_id, "log": log})
                        return
                    job = job_store.get(remainder)
                    if job is None:
                        self._send_error_json(404, "job not found")
                        return
                    self._send_json(serialize_job(job))
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
                    request = self._read_json()
                    profile_id = str(request.get("profile_id", ""))
                    start = str(request.get("start", ""))
                    end = str(request.get("end", ""))
                    if not profile_id or not start or not end:
                        raise ValueError("profile_id, start, and end are required")
                    output_path = request.get("output_path") or (
                        f"data/export_{start.replace('-', '')}_{end.replace('-', '')}_control"
                    )
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
                        str(output_path),
                    ]
                    job = runner.create_command_job(
                        "export_profile",
                        profile_id,
                        "create",
                        {
                            "profile_id": profile_id,
                            "start": start,
                            "end": end,
                            "output_path": output_path,
                        },
                        command,
                        cwd=active_root,
                        output_path=active_root / str(output_path),
                    )
                    self._send_json(serialize_job(job), status=202)
                    return
                if parsed.path == "/control/api/compatibility/model-run":
                    request = self._read_json()
                    model = _required_entry(registry, "model", str(request.get("model_id", "")))
                    dataset_path = (active_root / str(request.get("dataset_path", ""))).resolve()
                    if not _is_relative_to(dataset_path, active_root):
                        raise ValueError("dataset_path escapes repository root")
                    self._send_json(check_model_dataset_compatibility(model, dataset_path))
                    return
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_error_json(400, str(exc))
                return
            except Exception as exc:  # pragma: no cover - HTTP safety boundary.
                self._send_error_json(500, str(exc))
                return
            self._send_error_json(404, "not found")

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
        print(f"control API: http://{host}:{server.server_address[1]}/control/api")
        server.serve_forever()
    return 0


def _required_entry(registry: Registry, kind: str, entry_id: str):
    entry = registry.get(kind, entry_id)
    if entry is None:
        raise ValueError(f"registry entry not found: {kind}/{entry_id}")
    return entry


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("params must be an object")
    return value


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
