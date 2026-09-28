"""Phanes P1 — Experience structural contracts (Architecture Freeze v0.2, section 6).

Defines the fixed structural vocabulary of Experience schema v1: document
and record key sets, the two Experience kinds, provenance source classes,
dependency modes and the stable structural error codes.

This module defines structure only. The kind-by-dependency authority table
(Freeze section 8.2 / invariant 10), agent consistency, reference integrity
and all other semantic rules (Freeze section 10.2) are deliberately absent:
they belong to P1.2 semantic validation, not to the structural layer.

This module uses only the Python standard library and must not import
phanes_host, OS/hardware APIs or anything outside the standard library.
"""

from __future__ import annotations

EXPERIENCE_SCHEMA_VERSION = 1

# --- Structural error codes (Freeze section 12 failure codes) ---
INVALID_EXPERIENCE = "INVALID_EXPERIENCE"
UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
DUPLICATE_EXPERIENCE_ID = "DUPLICATE_EXPERIENCE_ID"
BROKEN_EXPERIENCE_REFERENCE = "BROKEN_EXPERIENCE_REFERENCE"
EXPERIENCE_REFERENCE_CYCLE = "EXPERIENCE_REFERENCE_CYCLE"
SUPERSESSION_SUBJECT_MISMATCH = "SUPERSESSION_SUBJECT_MISMATCH"
EXPERIENCE_NOT_FOUND = "EXPERIENCE_NOT_FOUND"

# --- Experience kinds (Freeze section 3 / invariant 7) ---
KIND_PLACE_OBSERVATION = "place_observation"
KIND_BODY_PARAMETER_OBSERVATION = "body_parameter_observation"
EXPERIENCE_KINDS = frozenset({KIND_PLACE_OBSERVATION, KIND_BODY_PARAMETER_OBSERVATION})

# --- Provenance source classes (Freeze section 7) ---
SOURCE_CLASS_USER_STATEMENT = "user_statement"
SOURCE_CLASS_ADAPTER_OBSERVATION = "adapter_observation"
SOURCE_CLASS_TEST_FIXTURE = "test_fixture"
PROVENANCE_SOURCE_CLASSES = frozenset(
    {SOURCE_CLASS_USER_STATEMENT, SOURCE_CLASS_ADAPTER_OBSERVATION, SOURCE_CLASS_TEST_FIXTURE}
)

# --- Dependency modes (Freeze section 8.1 global structural vocabulary) ---
MODE_NONE = "NONE"
MODE_EXACT = "EXACT"
MODE_UNKNOWN = "UNKNOWN"
MODE_INTERVAL = "INTERVAL"
SPATIAL_DEPENDENCY_MODES = frozenset({MODE_NONE, MODE_EXACT, MODE_UNKNOWN})
TEMPORAL_DEPENDENCY_MODES = frozenset({MODE_NONE, MODE_INTERVAL, MODE_UNKNOWN})

# --- Exact key sets (Freeze section 6: missing or extra fields are invalid) ---
DOCUMENT_KEYS = frozenset({"schema_version", "agent_id", "records"})
RECORD_KEYS = frozenset(
    {
        "experience_id",
        "agent_id",
        "kind",
        "payload",
        "provenance",
        "dependencies",
        "recorded_at",
        "observed_at",
        "derived_from",
        "supersedes",
    }
)
PLACE_PAYLOAD_KEYS = frozenset({"place_id", "position"})
POSITION_KEYS = frozenset({"x", "y"})
BODY_PARAMETER_PAYLOAD_KEYS = frozenset({"parameter_name", "value", "unit"})
PROVENANCE_KEYS = frozenset({"source_class", "source_ref"})
DEPENDENCIES_KEYS = frozenset({"body", "environment", "frame", "temporal"})

SPATIAL_DEPENDENCY_DIMENSIONS = ("body", "environment", "frame")
