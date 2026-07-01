"""Shared exception types."""

from __future__ import annotations


class NextGenError(RuntimeError):
    """Base exception for next-gen code."""


class ConfigError(NextGenError):
    """Raised when required configuration is missing or malformed."""


class DataValidationError(NextGenError):
    """Raised when collected or replayed data violates expected invariants."""


class SourceError(NextGenError):
    """Raised when an external source or local dataset cannot be read."""
