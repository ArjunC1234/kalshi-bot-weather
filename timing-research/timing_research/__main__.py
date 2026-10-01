"""CLI for quality auditing, protocol locking, sealed decisions, and delayed scoring."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .experiment import attach_outcomes, decide, summarize
from .reader import audit, digest, read_captures, stamp

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def write_json(path: Path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, default=str, allow_nan=False)
        stream.write("\n")


def source_hashes():
    return {
        str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted((ROOT / "timing_research").glob("*.py"))
    }


def protocol_at(experiment: Path):
    protocol = read_json(experiment / "PROTOCOL.json")
    start, end = stamp(protocol["start"]), stamp(protocol["end"])
    if end - start != timedelta(days=14) or start.hour or start.minute or start.second:
        raise ValueError("protocol must use 14 full UTC days")
    if stamp(protocol["score_not_before"]) < end + timedelta(days=3):
        raise ValueError("protocol requires a 72-hour settlement buffer")
    if protocol["live_trading"] is not False or protocol["horizons_minutes"] != [5, 15, 60]:
        raise ValueError("unsupported protocol")
    if (
        protocol["primary_horizon_minutes"] != 15
        or protocol["probe"]["side"] != "no"
        or protocol["probe"]["contracts"] != 1
    ):
        raise ValueError("unsupported probe or primary horizon")
    return protocol


def freeze(experiment: Path, now: datetime):
    protocol = protocol_at(experiment)
    if now >= stamp(protocol["start"]):
        raise ValueError("cannot preregister an experiment after its evaluation window has begun")
    lock = {
        "created_at": now.isoformat(),
        "protocol_sha256": digest(protocol),
        "source_hashes": source_hashes(),
        "python": platform.python_version(),
        "runtime": [sys.version_info.major, sys.version_info.minor],
        "protocol_notes_sha256": hashlib.sha256(
            (experiment / "PROTOCOL.md").read_bytes()
        ).hexdigest(),
        "live_trading_authorized": False,
    }
    lock["lock_sha256"] = digest(lock)
    write_json(experiment / "LOCK.json", lock)
    return lock


def check_lock(experiment: Path):
    protocol, lock = protocol_at(experiment), read_json(experiment / "LOCK.json")
    expected = {key: value for key, value in lock.items() if key != "lock_sha256"}
    if digest(expected) != lock["lock_sha256"] or digest(protocol) != lock["protocol_sha256"]:
        raise ValueError("protocol lock altered")
    if lock["source_hashes"] != source_hashes():
        raise ValueError("research code changed since freeze; create a new future experiment")
    if (
        lock["protocol_notes_sha256"]
        != hashlib.sha256((experiment / "PROTOCOL.md").read_bytes()).hexdigest()
    ):
        raise ValueError("protocol notes changed since freeze")
    if lock["runtime"] != [sys.version_info.major, sys.version_info.minor]:
        raise ValueError("use the frozen Python major/minor version")
    if stamp(lock["created_at"]) >= stamp(protocol["start"]):
        raise ValueError("late experiment freeze")
    return protocol, lock


def require_integrity(data):
    if data["integrity_errors"]:
        raise ValueError(f"input integrity errors; run audit first: {data['integrity_errors']}")


def report_markdown(summary):
    lines = [
        "# Timing Experiment",
        "",
        f"Status: {summary['status']}",
        "",
        "Observation-only hypothetical results; no live trades or guaranteed fills.",
        "",
        f"Revision events: {summary['revision_events']}; controls: {summary['controls']}; "
        f"settled probes: {summary['settled_probes']}.",
        "",
        "## Promotion Checks",
        "",
    ]
    lines.extend(
        f"- {key}: {'PASS' if value else 'FAIL'}" for key, value in summary["gate_checks"].items()
    )
    lines += [
        "",
        "## Daily Breakdown",
        "",
        "| UTC Date | Revisions | Price Pairs | Settled | Wins | Net PnL | Fees |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {row['date']} | {row['revisions']} | {row['price_pairs']} | "
        f"{row['settled_probes']} | {row['wins']} | {row['net_pnl']:.2f} | {row['fees']:.2f} |"
        for row in summary["daily"]
    )
    lines += [
        "",
        "Zero scored PnL with zero settled probes means no scored evidence, "
        "not breakeven performance.",
        "",
    ]
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "decide", "score"):
        command = sub.add_parser(name)
        command.add_argument("--experiment", type=Path, required=True)
        if name != "freeze":
            command.add_argument("--data", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
        if name == "decide":
            command.add_argument("--as-of")
        if name == "score":
            command.add_argument("--decisions", type=Path, required=True)
            command.add_argument("--settlements", type=Path)
    quality = sub.add_parser("audit")
    quality.add_argument("--data", type=Path, required=True)
    quality.add_argument("--start", required=True)
    quality.add_argument("--end", required=True)
    quality.add_argument("--as-of")
    quality.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    if sys.version_info[:2] != (3, 12):
        raise ValueError("this research package requires Python 3.12")
    if args.command == "freeze":
        print(json.dumps(freeze(args.experiment, now), indent=2))
        return 0
    if args.command == "audit":
        as_of = stamp(args.as_of) if args.as_of else now
        if as_of > now or stamp(args.end) > as_of:
            raise ValueError("audit bounds cannot extend beyond availability cutoff/current time")
        data = read_captures(args.data, as_of)
        quality = audit(data, stamp(args.start), stamp(args.end))
        args.output.mkdir(parents=True, exist_ok=False)
        write_json(args.output / "AUDIT.json", quality)
        write_json(args.output / "INPUTS.json", data["files"])
        write_json(args.output / "OBSERVATIONS.json", data["observations"])
        write_json(
            args.output / "PROVENANCE.json",
            {
                "created_at": now.isoformat(),
                "as_of": as_of.isoformat(),
                "source_hashes": source_hashes(),
                "python": platform.python_version(),
            },
        )
        print(json.dumps(quality, indent=2))
        return int(
            bool(quality["integrity_errors"])
            or quality["missing_cycles"] > 0
            or quality["provider_failures"] > 0
            or quality["valid_city_cycle_coverage"] < 1
        )
    protocol, lock = check_lock(args.experiment)
    if args.command == "score" and now < stamp(protocol["score_not_before"]):
        raise ValueError(
            f"scoring is embargoed until {protocol['score_not_before']}; use audit only"
        )
    as_of = (
        (stamp(args.as_of) if args.as_of else now)
        if args.command == "decide"
        else stamp(protocol["score_not_before"])
    )
    if as_of > now:
        raise ValueError("cannot claim a future availability cutoff")
    data = read_captures(args.data, as_of)
    require_integrity(data)
    if args.command == "decide":
        decisions = decide(data["observations"], protocol)
        artifact = {
            "lock_sha256": lock["lock_sha256"],
            "as_of": as_of.isoformat(),
            "created_at": now.isoformat(),
            "inputs": data["files"],
            "decisions": decisions,
            "status": "awaiting_evaluation_window"
            if as_of < stamp(protocol["start"])
            else "sealed_replay",
            "note": "Predeclared as-of replay, not a live paper-order log.",
        }
        artifact["artifact_sha256"] = digest(artifact)
        args.output.mkdir(parents=True, exist_ok=False)
        write_json(args.output / "DECISIONS.json", artifact)
        print(
            json.dumps(
                {
                    "status": artifact["status"],
                    "intentions": len(decisions["intentions"]),
                    "controls": len(decisions["controls"]),
                    "output": str(args.output),
                },
                indent=2,
            )
        )
        return 0
    saved = read_json(args.decisions / "DECISIONS.json")
    if saved["artifact_sha256"] != digest(
        {key: value for key, value in saved.items() if key != "artifact_sha256"}
    ):
        raise ValueError("sealed decisions modified")
    if saved["lock_sha256"] != lock["lock_sha256"]:
        raise ValueError("decision protocol differs")
    if not stamp(protocol["end"]) <= stamp(saved["as_of"]) <= stamp(protocol["score_not_before"]):
        raise ValueError(
            "decisions must cover the complete window, with cutoff no later than scoring deadline"
        )
    if not {item["sha256"] for item in saved["inputs"]} <= {
        item["sha256"] for item in data["files"]
    }:
        raise ValueError("sealed input files changed or disappeared")
    prefix = read_captures(args.data, stamp(saved["as_of"]))
    require_integrity(prefix)
    if digest(decide(prefix["observations"], protocol)) != digest(saved["decisions"]):
        raise ValueError("decision replay changed under later data")
    labels = read_json(args.settlements) if args.settlements else []
    outcomes = attach_outcomes(saved["decisions"], data["observations"], labels, protocol)
    quality = audit(data, stamp(protocol["start"]), stamp(protocol["end"]))
    summary = summarize(saved["decisions"], outcomes, quality, protocol)
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "OUTCOMES.json", outcomes)
    write_json(args.output / "SUMMARY.json", summary)
    write_json(args.output / "QUALITY.json", quality)
    write_json(
        args.output / "PROVENANCE.json",
        {
            "lock_sha256": lock["lock_sha256"],
            "decision_artifact_sha256": saved["artifact_sha256"],
            "inputs": data["files"],
            "settlements_sha256": hashlib.sha256(args.settlements.read_bytes()).hexdigest()
            if args.settlements
            else None,
        },
    )
    (args.output / "REPORT.md").write_text(report_markdown(summary), encoding="utf-8")
    print(json.dumps({"status": summary["status"], "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from None
