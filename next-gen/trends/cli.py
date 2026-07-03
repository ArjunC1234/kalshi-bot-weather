"""CLI for the trends GUI."""

from __future__ import annotations

import argparse
import json
import socketserver
import webbrowser
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from trends.datasets import build_workbench_payload
from trends.sources import SourceRoots, discover_sources, resolve_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explore collected weather-market trends")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="start the local trends GUI")
    serve.add_argument("--data-root", type=Path, default=Path("data"))
    serve.add_argument("--report-root", type=Path, default=Path("reports/model"))
    serve.add_argument("--quality-root", type=Path, default=Path("reports/quality"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve_gui(
            SourceRoots(args.data_root, args.report_root, args.quality_root),
            args.host,
            args.port,
            open_browser=not args.no_open,
        )
    return 1


def serve_gui(
    roots: SourceRoots,
    host: str,
    port: int,
    open_browser: bool = True,
) -> int:
    static_dir = Path(__file__).resolve().parent / "static"
    normalized_roots = roots.normalized()
    active: dict[str, Any] = {"payload": None, "selection": None, "cache_key": None}

    class TrendsHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, directory=str(static_dir), **kwargs)

        def do_GET(self) -> None:  # noqa: N802 - stdlib API name.
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/sources":
                    self._send_json(discover_sources(normalized_roots))
                    return
                if parsed.path == "/api/catalog":
                    payload = self._require_payload()
                    self._send_json(
                        {
                            **payload["catalog"],
                            "metadata": payload["metadata"],
                            "selection": active["selection"],
                        }
                    )
                    return
                if parsed.path == "/api/overview":
                    payload = self._require_payload()
                    self._send_json(payload["analysis"]["overview"])
                    return
                if parsed.path.startswith("/api/series/"):
                    metric = unquote(parsed.path.removeprefix("/api/series/"))
                    payload = self._require_payload()
                    self._send_json({"metric": metric, "rows": payload["series"].get(metric, [])})
                    return
                if parsed.path.startswith("/api/analysis/"):
                    section = unquote(parsed.path.removeprefix("/api/analysis/"))
                    payload = self._require_payload()
                    if section not in payload["analysis"]:
                        self._send_error_json(404, f"analysis section not found: {section}")
                        return
                    self._send_json({"section": section, "data": payload["analysis"][section]})
                    return
                if parsed.path.startswith("/api/event/"):
                    event_key = unquote(parsed.path.removeprefix("/api/event/"))
                    event = _find_event(self._require_payload(), event_key)
                    if event is None:
                        self._send_error_json(404, f"event not found: {event_key}")
                        return
                    self._send_json(event)
                    return
                if parsed.path.startswith("/api/table/"):
                    table_name = unquote(parsed.path.removeprefix("/api/table/"))
                    payload = self._require_payload()
                    table = payload["tables"].get(table_name)
                    if table is None:
                        self._send_error_json(404, f"table not found: {table_name}")
                        return
                    self._send_json({"name": table_name, "rows": table})
                    return
            except RuntimeError as exc:
                self._send_error_json(409, str(exc))
                return
            except ValueError as exc:
                self._send_error_json(400, str(exc))
                return
            if parsed.path == "/":
                self.path = "/index.html"
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802 - stdlib API name.
            parsed = urlparse(self.path)
            if parsed.path != "/api/load":
                self._send_error_json(404, "not found")
                return
            try:
                request = self._read_json()
                data_path = resolve_source(normalized_roots, "export", request.get("export_id"))
                if data_path is None:
                    raise ValueError("export_id is required")
                report_path = resolve_source(normalized_roots, "report", request.get("report_id"))
                quality_path = resolve_source(
                    normalized_roots,
                    "quality",
                    request.get("quality_id"),
                )
                cache_key = _cache_key([data_path, report_path, quality_path])
                if active["cache_key"] != cache_key:
                    active["payload"] = build_workbench_payload(
                        data_path,
                        [report_path] if report_path else [],
                        quality_path,
                    )
                    active["cache_key"] = cache_key
                active["selection"] = {
                    "export_id": request.get("export_id"),
                    "report_id": request.get("report_id") or "",
                    "quality_id": request.get("quality_id") or "",
                    "data_path": str(data_path),
                    "report_path": str(report_path) if report_path else None,
                    "quality_path": str(quality_path) if quality_path else None,
                }
                payload = active["payload"]
                self._send_json(
                    {
                        "metadata": payload["metadata"],
                        "catalog": payload["catalog"],
                        "overview": payload["analysis"]["overview"],
                        "selection": active["selection"],
                    }
                )
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_error_json(400, str(exc))
            except Exception as exc:  # pragma: no cover - defensive API boundary.
                self._send_error_json(500, f"failed to load workbench: {exc}")

        def _require_payload(self) -> dict[str, Any]:
            payload = active.get("payload")
            if not isinstance(payload, dict):
                raise RuntimeError("no Trends source loaded")
            return payload

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("request body must be a JSON object")
            return value

        def _send_json(self, value: object, status: int = 200) -> None:
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json({"error": message}, status=status)

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

    with socketserver.TCPServer((host, port), TrendsHandler) as server:
        url = f"http://{host}:{port}"
        print(f"trends GUI: {url}")
        print(f"data root: {normalized_roots.data_root}")
        print(f"report root: {normalized_roots.report_root}")
        print(f"quality root: {normalized_roots.quality_root}")
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()
    return 0


def _find_event(payload: dict[str, Any], event_key: str) -> dict[str, Any] | None:
    events = payload.get("analysis", {}).get("event_replays", [])
    for event in events:
        if isinstance(event, dict) and event.get("event_key") == event_key:
            return event
    return None


def _cache_key(paths: list[Path | None]) -> tuple[tuple[str, float], ...]:
    return tuple((str(path), _newest_mtime(path)) for path in paths if path is not None)


def _newest_mtime(path: Path) -> float:
    newest = path.stat().st_mtime
    for child in path.iterdir():
        if child.is_file():
            newest = max(newest, child.stat().st_mtime)
    return newest


if __name__ == "__main__":
    raise SystemExit(main())
