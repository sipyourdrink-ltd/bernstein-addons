"""Strict report validation against the bundled report.schema.json."""

from __future__ import annotations

import json
import math
import re
from importlib import resources
from typing import Any


class ReportSchemaError(ValueError):
    """Raised when an emitted StallReport violates report.schema.json."""


_CACHED_SCHEMA: dict[str, Any] | None = None


def load_report_schema() -> dict[str, Any]:
    """Load the bundled JSON Schema for MergeQueueStallReport."""
    global _CACHED_SCHEMA
    if _CACHED_SCHEMA is None:
        raw = (
            resources.files("bernstein_merge_queue_stall_guard")
            .joinpath("report.schema.json")
            .read_text(encoding="utf-8")
        )
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ReportSchemaError("bundled report.schema.json is not a JSON object")
        _CACHED_SCHEMA = parsed
    return _CACHED_SCHEMA


def _matches_type(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return (
            isinstance(instance, (int, float))
            and not isinstance(instance, bool)
            and math.isfinite(float(instance))
        )
    if expected == "string":
        return isinstance(instance, str)
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    return False


def _validate_node(instance: Any, schema: dict[str, Any], path: str) -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        types = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        if not any(_matches_type(instance, t) for t in types):
            raise ReportSchemaError(
                f"{path}: expected type {types}, got {type(instance).__name__} ({instance!r})"
            )

    if "const" in schema and instance != schema["const"]:
        raise ReportSchemaError(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        raise ReportSchemaError(f"{path}: {instance!r} not in {schema['enum']!r}")

    if isinstance(instance, str):
        min_len = schema.get("minLength")
        if isinstance(min_len, int) and len(instance) < min_len:
            raise ReportSchemaError(f"{path}: length {len(instance)} < minLength {min_len}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            raise ReportSchemaError(f"{path}: {instance!r} does not match pattern {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if not math.isfinite(float(instance)):
            raise ReportSchemaError(f"{path}: non-finite number {instance!r}")
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            raise ReportSchemaError(f"{path}: {instance!r} < minimum {minimum!r}")
        exc_min = schema.get("exclusiveMinimum")
        if isinstance(exc_min, (int, float)) and instance <= exc_min:
            raise ReportSchemaError(f"{path}: {instance!r} <= exclusiveMinimum {exc_min!r}")

    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                raise ReportSchemaError(f"{path}: missing required key {req!r}")
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        for key, val in instance.items():
            if not isinstance(key, str):
                raise ReportSchemaError(f"{path}: non-string key {key!r}")
            if key in props:
                _validate_node(val, props[key], f"{path}.{key}")
            elif addl is False:
                raise ReportSchemaError(f"{path}: unexpected property {key!r}")
            elif isinstance(addl, dict):
                _validate_node(val, addl, f"{path}.{key}")

    if isinstance(instance, list) and "items" in schema:
        item_schema = schema["items"]
        for idx, elem in enumerate(instance):
            _validate_node(elem, item_schema, f"{path}[{idx}]")


def validate_report_dict(instance: dict[str, Any]) -> None:
    """Validate a serialized StallReport against report.schema.json.

    Always runs the zero-dependency built-in validator and also runs
    ``jsonschema`` when available in the environment.
    """
    schema = load_report_schema()
    _validate_node(instance, schema, "$")
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        return
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except Exception as exc:  # noqa: BLE001
        raise ReportSchemaError(f"jsonschema validation failed: {exc}") from exc
