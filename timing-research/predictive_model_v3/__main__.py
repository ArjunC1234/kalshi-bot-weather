from __future__ import annotations

import argparse
from pathlib import Path

from .model import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run rolling walk-forward v3 research")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path(__file__).with_name("REQUIREMENTS.json"),
    )
    args = parser.parse_args()
    result = run(args.data_dir, args.output_dir, args.requirements)
    print(result["status"])
    print(result["status_reason"])
    if result.get("selected_policy"):
        print(result["selected_policy"]["policy_id"])


if __name__ == "__main__":
    main()
