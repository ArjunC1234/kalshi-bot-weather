"""Match inference columns to the original model's lightweight training export."""

import argparse
import json
from pathlib import Path

from control.providers.supabase_export import _filter_row
from libs.io_utils import write_json_gz
from libs.json_utils import write_json
from scripts.hit80_14d_research import _load_json_gz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    profile = json.loads(args.profile.read_text())
    for name, spec in profile["tables"].items():
        path = args.data / f"{name}.json.gz"
        if spec.get("include") is False or not path.exists():
            continue
        rows = [_filter_row(row, spec) for row in _load_json_gz(path)]
        write_json_gz(args.output / path.name, rows)
    write_json(args.output / "manifest.json", {"parent": str(args.data), "profile": str(args.profile)})
    write_json(args.output / "export_profile.json", profile)


if __name__ == "__main__":
    main()
