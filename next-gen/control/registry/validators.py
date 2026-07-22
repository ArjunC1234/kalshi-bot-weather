"""Small dependency-free validators for control registry entries."""

from __future__ import annotations

from typing import Any


class RegistryValidationError(ValueError):
    """Raised when a registry entry is malformed."""


def validate_registry_entry(spec: dict[str, Any]) -> None:
    _required_text(spec, "id")
    _required_text(spec, "kind")
    _required_text(spec, "label")
    kind = spec["kind"]
    if kind == "model":
        _validate_model(spec)
    elif kind == "export_profile":
        _validate_export_profile(spec)
    elif kind == "visualization":
        _validate_visualization(spec)
    elif kind == "data_source":
        _required_text(spec, "provider")
    elif kind == "bot_runtime":
        _required_text(spec, "provider")
    elif kind == "strategy":
        _validate_model(spec)
    else:
        raise RegistryValidationError(f"unsupported registry kind: {kind}")


def _validate_model(spec: dict[str, Any]) -> None:
    entrypoints = spec.get("entrypoints")
    if not isinstance(entrypoints, dict) or not entrypoints:
        raise RegistryValidationError("model/strategy entry must define entrypoints")
    for name, entrypoint in entrypoints.items():
        if not isinstance(entrypoint, dict):
            raise RegistryValidationError(f"entrypoint {name} must be an object")
        command = entrypoint.get("command")
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            raise RegistryValidationError(f"entrypoint {name} command must be a string list")
    inputs = spec.get("inputs", {})
    if inputs and not isinstance(inputs, dict):
        raise RegistryValidationError("inputs must be an object")


def _validate_export_profile(spec: dict[str, Any]) -> None:
    _required_text(spec, "source")
    tables = spec.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise RegistryValidationError("export_profile must define tables")
    for name, table in tables.items():
        if not isinstance(table, dict):
            raise RegistryValidationError(f"table {name} must be an object")
        if table.get("include") is False:
            continue
        if not isinstance(table.get("source_table", name), str):
            raise RegistryValidationError(f"table {name} source_table must be text")


def _validate_visualization(spec: dict[str, Any]) -> None:
    _required_text(spec, "artifact_type")
    views = spec.get("views")
    if not isinstance(views, dict) or not views:
        raise RegistryValidationError("visualization must define views")


def _required_text(spec: dict[str, Any], key: str) -> None:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryValidationError(f"missing required text field: {key}")

