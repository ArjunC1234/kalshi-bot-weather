"""Load model, export, visualization, and bot registry entries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from control.registry.validators import RegistryValidationError, validate_registry_entry

REGISTRY_KINDS = (
    "data_source",
    "export_profile",
    "model",
    "strategy",
    "visualization",
    "bot_runtime",
)


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    kind: str
    path: Path
    spec: dict[str, Any]

    @property
    def label(self) -> str:
        return str(self.spec.get("label") or self.id)

    @property
    def version(self) -> int:
        value = self.spec.get("version")
        return int(value) if isinstance(value, int | float | str) and str(value).isdigit() else 1


@dataclass(frozen=True)
class Registry:
    root: Path
    entries: tuple[RegistryEntry, ...]

    def by_kind(self, kind: str) -> list[RegistryEntry]:
        return [entry for entry in self.entries if entry.kind == kind]

    def get(self, kind: str, entry_id: str) -> RegistryEntry | None:
        return next(
            (entry for entry in self.entries if entry.kind == kind and entry.id == entry_id),
            None,
        )

    def as_dict(self) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {kind: [] for kind in REGISTRY_KINDS}
        for entry in self.entries:
            payload = {
                "id": entry.id,
                "kind": entry.kind,
                "path": str(entry.path),
                **entry.spec,
            }
            grouped.setdefault(entry.kind, []).append(payload)
        return {"root": str(self.root), "entries": grouped}


def load_registry(root: Path | None = None) -> Registry:
    """Load built-in and optional overlay registry JSON files."""

    builtin = Path(__file__).resolve().parent / "builtin"
    roots = [builtin]
    if root is not None:
        roots.append(root)
    entries: list[RegistryEntry] = []
    seen: set[tuple[str, str]] = set()
    for active_root in roots:
        if not active_root.exists():
            continue
        for path in sorted(active_root.rglob("*.json")):
            if path.name.endswith(".schema.json") or path.parts[-2:] == ("schemas", path.name):
                continue
            entry = _load_entry(path)
            key = (entry.kind, entry.id)
            if key in seen:
                entries = [item for item in entries if (item.kind, item.id) != key]
            seen.add(key)
            entries.append(entry)
    return Registry(root=builtin if root is None else root, entries=tuple(entries))


def validate_registry(root: Path | None = None) -> list[str]:
    """Return validation error messages for all registry entries."""

    errors: list[str] = []
    builtin = Path(__file__).resolve().parent / "builtin"
    roots = [builtin]
    if root is not None:
        roots.append(root)
    for active_root in roots:
        if not active_root.exists():
            continue
        for path in sorted(active_root.rglob("*.json")):
            if path.name.endswith(".schema.json") or "schemas" in path.parts:
                continue
            try:
                _load_entry(path)
            except (OSError, ValueError, RegistryValidationError) as exc:
                errors.append(f"{path}: {exc}")
    return errors


def _load_entry(path: Path) -> RegistryEntry:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("registry entry must be a JSON object")
    validate_registry_entry(value)
    return RegistryEntry(
        id=str(value["id"]),
        kind=str(value["kind"]),
        path=path,
        spec=value,
    )

