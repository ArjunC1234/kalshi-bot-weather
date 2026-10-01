from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .model import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run monotone-barrier model v1")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path(__file__).with_name("REQUIREMENTS.json"),
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    result = run(args.data_dir, args.output_dir, args.requirements, args.as_of)
    print(result["status"])
    print(result["status_reason"])
    if result["selected_policy"]:
        print(result["selected_policy"]["policy_id"])


if __name__ == "__main__":
    main()
