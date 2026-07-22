"""Command line entrypoint for the registry-driven control plane."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from control.artifacts.index import ArtifactIndex, scan_artifacts
from control.providers.supabase_export import export_with_profile
from control.registry.loader import load_registry, validate_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kalshi weather control plane")
    commands = parser.add_subparsers(dest="command", required=True)

    registry = commands.add_parser("registry", help="Inspect or validate registry entries.")
    registry.add_argument("action", choices=["list", "validate"])
    registry.add_argument("--root", type=Path)

    artifacts = commands.add_parser("artifacts", help="Scan and index local artifacts.")
    artifacts.add_argument("action", choices=["scan"])
    artifacts.add_argument("--index", type=Path, default=Path(".control/artifacts.sqlite"))
    artifacts.add_argument("--data-root", type=Path, default=Path("data"))
    artifacts.add_argument("--model-root", type=Path, default=Path("reports/model"))
    artifacts.add_argument("--quality-root", type=Path, default=Path("reports/quality"))
    artifacts.add_argument("--strategy-root", type=Path, default=Path("reports/strategy"))

    export = commands.add_parser("export", help="Create a local export from a registry profile.")
    export.add_argument("--profile", required=True)
    export.add_argument("--start", required=True)
    export.add_argument("--end", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--registry-root", type=Path)

    serve = commands.add_parser("serve", help="Start the control-plane HTTP API.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8775)
    serve.add_argument("--registry-root", type=Path)

    args = parser.parse_args(argv)
    if args.command == "registry":
        if args.action == "validate":
            errors = validate_registry(args.root)
            if errors:
                print("\n".join(errors))
                return 1
            print("registry valid")
            return 0
        active = load_registry(args.root)
        print(json.dumps(active.as_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "artifacts":
        records = scan_artifacts(
            args.data_root,
            args.model_root,
            args.quality_root,
            args.strategy_root,
        )
        index = ArtifactIndex(args.index)
        index.replace_all(records)
        print(f"indexed {len(records)} artifacts")
        return 0
    if args.command == "export":
        active = load_registry(args.registry_root)
        profile = active.get("export_profile", args.profile)
        if profile is None:
            raise SystemExit(f"export profile not found: {args.profile}")
        export_with_profile(profile, args.start, args.end, args.output)
        print(f"export complete: {args.output}")
        return 0
    if args.command == "serve":
        from control.server import serve_control

        return serve_control(
            host=args.host,
            port=args.port,
            registry_root=args.registry_root,
            repo_root=Path("."),
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
