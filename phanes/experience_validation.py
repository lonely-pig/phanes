"""Phanes P1 — Experience structural validator (Architecture Freeze v0.2, section 10.1).

Stdlib-only strict structural validation for Experience schema v1. This
module answers exactly one question:

    Is this JSON value structurally a legal P1 Experience v1 document?

It deliberately does NOT implement semantic validation (Freeze section
10.2): document/record/Identity agent consistency, the kind-by-dependency
authority table (Freeze section 8.2 / invariant 10), Experience ID
uniqueness, reference existence and integrity, derivation/supersession
cycles, supersession subject identity, and temporal bound ordering are all
out of scope here. Structurally valid input may still be semantically
invalid and must be rejected by a later layer before evaluation.

The normative structural specification is the JSON Schema Draft 2020-12
document at schemas/phanes-experience-v1.schema.json; this validator must
accept the same structural input set, demonstrated by the shared
conformance fixtures under tests/fixtures/experience_v1/.

The validator never mutates its input, never normalizes opaque
identifiers, never supplies defaults, performs no I/O and fails closed on
any structural deviation.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime
from typing import NoReturn

from phanes.experience_contracts import (
    BODY_PARAMETER_PAYLOAD_KEYS,
    DEPENDENCIES_KEYS,
    DOCUMENT_KEYS,
    EXPERIENCE_KINDS,
    EXPERIENCE_SCHEMA_VERSION,
    INVALID_EXPERIENCE,
    KIND_PLACE_OBSERVATION,
    MODE_EXACT,
    MODE_INTERVAL,
    PLACE_PAYLOAD_KEYS,
    POSITION_KEYS,
    PROVENANCE_KEYS,
    PROVENANCE_SOURCE_CLASSES,
    RECORD_KEYS,
    SPATIAL_DEPENDENCY_DIMENSIONS,
    SPATIAL_DEPENDENCY_MODES,
    TEMPORAL_DEPENDENCY_MODES,
    UNSUPPORTED_SCHEMA_VERSION,
)

# UTC RFC 3339 input acceptance: the 'Z' designator and the equivalent
# '+00:00' offset both express UTC and are both accepted, with optional
# fractional seconds. Naive timestamps, non-zero offsets and the
# negative-zero offset ('-00:00', unknown offset per RFC 3339) are
# rejected. The pattern bounds each field lexically; full calendar
# validity (day-per-month, leap years) is verified with
# datetime.fromisoformat afterwards, matching the normative schema's
# pattern + "format": "date-time" pair. Writers may still canonicalize
# output to 'Z'; output canonicalization is not input acceptance.
_UTC_RFC3339_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|\+00:00)$"
)


def _is_finite_json_number(value: object) -> bool:
    """Structural JSON number predicate: bool is not a number, any Python
    int is a finite JSON number (no implicit float conversion), and a float
    qualifies only when finite. Everything else is rejected."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


class ExperienceValidationError(ValueError):
    """Structural validation failure carrying a stable error code."""

    def __init__(self, code: str, diagnostic: str) -> None:
        super().__init__(f"{code}: {diagnostic}")
        self.code = code
        self.diagnostic = diagnostic


def validate_experience_document_structure(value: object) -> None:
    """Raise ExperienceValidationError unless value is a structurally valid
    Experience schema v1 document.

    Pure structural check: no mutation, no I/O, no semantic rules.
    """
    if not isinstance(value, dict):
        _fail("$", f"document must be a JSON object, got {_type_name(value)}")
    _require_exact_keys(value, DOCUMENT_KEYS, "$")
    schema_version = value["schema_version"]
    # JSON Schema numeric equality: the JSON numbers 1 and 1.0 are equal,
    # so both are accepted; bool is not a JSON number and is excluded.
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, (int, float))
        or schema_version != EXPERIENCE_SCHEMA_VERSION
    ):
        raise ExperienceValidationError(
            UNSUPPORTED_SCHEMA_VERSION,
            f"$.schema_version: expected {EXPERIENCE_SCHEMA_VERSION}, got {schema_version!r}",
        )
    _require_canonical_uuid(value["agent_id"], "$.agent_id")
    records = value["records"]
    if not isinstance(records, list):
        _fail("$.records", f"must be an array, got {_type_name(records)}")
    for index, record in enumerate(records):
        _validate_record(record, f"$.records[{index}]")


# --- Record ---


def _validate_record(record: object, path: str) -> None:
    if not isinstance(record, dict):
        _fail(path, f"record must be a JSON object, got {_type_name(record)}")
    _require_exact_keys(record, RECORD_KEYS, path)
    _require_canonical_uuid(record["experience_id"], f"{path}.experience_id")
    _require_canonical_uuid(record["agent_id"], f"{path}.agent_id")
    kind = record["kind"]
    if not isinstance(kind, str) or kind not in EXPERIENCE_KINDS:
        _fail(f"{path}.kind", f"unsupported experience kind: {kind!r}")
    _validate_payload(kind, record["payload"], f"{path}.payload")
    _validate_provenance(record["provenance"], f"{path}.provenance")
    _validate_dependencies(record["dependencies"], f"{path}.dependencies")
    _require_timestamp(record["recorded_at"], f"{path}.recorded_at")
    observed_at = record["observed_at"]
    if observed_at is not None:
        _require_timestamp(observed_at, f"{path}.observed_at")
    _validate_derived_from(record["derived_from"], f"{path}.derived_from")
    supersedes = record["supersedes"]
    if supersedes is not None:
        _require_canonical_uuid(supersedes, f"{path}.supersedes")


def _validate_payload(kind: str, payload: object, path: str) -> None:
    if not isinstance(payload, dict):
        _fail(path, f"payload must be a JSON object, got {_type_name(payload)}")
    if kind == KIND_PLACE_OBSERVATION:
        _require_exact_keys(payload, PLACE_PAYLOAD_KEYS, path)
        _require_non_empty_string(payload["place_id"], f"{path}.place_id")
        position = payload["position"]
        position_path = f"{path}.position"
        if not isinstance(position, dict):
            _fail(position_path, f"position must be a JSON object, got {_type_name(position)}")
        _require_exact_keys(position, POSITION_KEYS, position_path)
        for axis in sorted(POSITION_KEYS):
            _require_finite_number(position[axis], f"{position_path}.{axis}")
    else:
        _require_exact_keys(payload, BODY_PARAMETER_PAYLOAD_KEYS, path)
        _require_non_empty_string(payload["parameter_name"], f"{path}.parameter_name")
        _require_finite_number(payload["value"], f"{path}.value")
        _require_non_empty_string(payload["unit"], f"{path}.unit")


def _validate_provenance(provenance: object, path: str) -> None:
    if not isinstance(provenance, dict):
        _fail(path, f"provenance must be a JSON object, got {_type_name(provenance)}")
    _require_exact_keys(provenance, PROVENANCE_KEYS, path)
    source_class = provenance["source_class"]
    if not isinstance(source_class, str) or source_class not in PROVENANCE_SOURCE_CLASSES:
        _fail(f"{path}.source_class", f"unsupported source class: {source_class!r}")
    _require_non_empty_string(provenance["source_ref"], f"{path}.source_ref")


# --- Dependencies (global structural vocabulary only; the kind-by-dependency
# --- authority table of Freeze section 8.2 is P1.2 semantic validation) ---


def _validate_dependencies(dependencies: object, path: str) -> None:
    if not isinstance(dependencies, dict):
        _fail(path, f"dependencies must be a JSON object, got {_type_name(dependencies)}")
    _require_exact_keys(dependencies, DEPENDENCIES_KEYS, path)
    for dimension in SPATIAL_DEPENDENCY_DIMENSIONS:
        _validate_spatial_dependency(dependencies[dimension], f"{path}.{dimension}")
    _validate_temporal_dependency(dependencies["temporal"], f"{path}.temporal")


def _validate_spatial_dependency(value: object, path: str) -> None:
    mode = _dependency_mode(value, SPATIAL_DEPENDENCY_MODES, path)
    if mode == MODE_EXACT:
        _require_exact_keys(value, {"mode", "id"}, path)
        _require_non_empty_string(value["id"], f"{path}.id")
    else:
        _require_exact_keys(value, {"mode"}, path)


def _validate_temporal_dependency(value: object, path: str) -> None:
    mode = _dependency_mode(value, TEMPORAL_DEPENDENCY_MODES, path)
    if mode == MODE_INTERVAL:
        _require_exact_keys(value, {"mode", "valid_from", "valid_until"}, path)
        _require_timestamp(value["valid_from"], f"{path}.valid_from")
        _require_timestamp(value["valid_until"], f"{path}.valid_until")
        # valid_from < valid_until ordering is semantic validation
        # (Freeze section 8.3) and is intentionally not checked here.
    else:
        _require_exact_keys(value, {"mode"}, path)


def _dependency_mode(value: object, allowed: frozenset[str], path: str) -> str:
    if not isinstance(value, dict):
        _fail(path, f"dependency must be a JSON object, got {_type_name(value)}")
    if "mode" not in value:
        _fail(path, "missing field(s): ['mode']")
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in allowed:
        _fail(f"{path}.mode", f"unsupported dependency mode: {mode!r}")
    return mode


# --- Relations ---


def _validate_derived_from(value: object, path: str) -> None:
    if not isinstance(value, list):
        _fail(path, f"must be an array, got {_type_name(value)}")
    for index, item in enumerate(value):
        _require_canonical_uuid(item, f"{path}[{index}]")


# --- Leaf predicates ---


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return str(parsed) == value


def _is_utc_rfc3339_timestamp(value: object) -> bool:
    if not isinstance(value, str) or _UTC_RFC3339_RE.match(value) is None:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


# --- Small requirement helpers ---


def _require_exact_keys(obj: dict, expected: frozenset[str], path: str) -> None:
    # A JSON object key is always a string; a Python dict carrying
    # non-string keys is not a legal JSON object representation and is
    # rejected, never normalized.
    if not all(isinstance(key, str) for key in obj):
        _fail(path, "object keys must all be strings")
    keys = set(obj)
    missing = expected - keys
    extra = keys - expected
    if missing:
        _fail(path, f"missing field(s): {sorted(missing)}")
    if extra:
        _fail(path, f"unexpected field(s): {sorted(extra)}")


def _require_non_empty_string(value: object, path: str) -> None:
    if not isinstance(value, str) or value == "":
        _fail(path, f"must be a non-empty string, got {value!r}")


def _require_finite_number(value: object, path: str) -> None:
    if not _is_finite_json_number(value):
        _fail(path, f"must be a finite JSON number, got {value!r}")


def _require_canonical_uuid(value: object, path: str) -> None:
    if not _is_canonical_uuid(value):
        _fail(path, f"must be a canonical UUID string, got {value!r}")


def _require_timestamp(value: object, path: str) -> None:
    if not _is_utc_rfc3339_timestamp(value):
        _fail(path, f"must be a UTC RFC 3339 timestamp, got {value!r}")


def _type_name(value: object) -> str:
    return type(value).__name__


def _fail(path: str, message: str) -> NoReturn:
    raise ExperienceValidationError(INVALID_EXPERIENCE, f"{path}: {message}")
