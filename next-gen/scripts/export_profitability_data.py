"""Read-only export of fresh data; display coverage without displaying outcomes."""

import argparse
from copy import deepcopy
from pathlib import Path

from control.providers.supabase_export import export_with_profile
from control.registry.loader import RegistryEntry, load_registry
from libs.supabase_client import SupabaseClient


class OrderedExportClient(SupabaseClient):
    """Closed historical ranges need deterministic ordering across REST pages."""

    def select(self, table, params=None):
        primary_keys = {"events": "event_id", "weather_snapshots": "weather_snapshot_id",
                        "market_snapshots": "market_snapshot_id", "settlements": "settlement_id",
                        "final_temperature_labels": "final_temperature_label_id"}
        key = primary_keys[table]
        rows = super().select(table, {**(params or {}), "order": f"{key}.asc"})
        if len({row[key] for row in rows}) != len(rows):
            raise ValueError(f"Repeated primary keys in paginated {table} export")
        return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = load_registry().get("export_profile", "lightweight_model_eval")
    spec = deepcopy(profile.spec)
    # Preserve availability timestamps, market rules, and depth when stored.
    for table in spec["tables"].values():
        table.pop("required_columns", None)
        table.pop("protected_columns", None)
    profile = RegistryEntry("profitability_audit", "export_profile", profile.path, spec)
    export_with_profile(profile, args.start, args.end, args.output,
                        client=OrderedExportClient.from_env())
    print(f"Export complete: {args.output}")


if __name__ == "__main__":
    main()
