"""Phanes P1.2 — Experience semantic validation (Architecture Freeze v0.2,
sections 8.2, 8.3, 10.2 and 13).

Semantic validation runs after structural validation
(phanes/experience_validation.py) and before any applicability evaluation
(P1.3, not implemented here). It checks the rules a structurally valid
Experience v1 document must still satisfy:

- Identity/document/record agent consistency (Freeze section 6);
- the kind-by-dependency authority table (Freeze section 8.2 / invariant
  10), the sole rule for kind-specific dependency validity;
- unique Experience IDs within the document;
- reference existence and integrity: every derived_from parent and every
  supersedes target must exist in the same complete document; derived_from
  parents are unique;
- derivation and supersession acyclicity, including self-reference;
- supersession subject identity (kind plus the frozen subject of section
  13) and non-branching (at most one direct successor per record);
- temporal ordering: an INTERVAL requires valid_from < valid_until.

This layer still does not implement applicability evaluation, TargetContext
or any operational path. It performs no I/O, never mutates its input and
fails closed with stable error codes (Freeze section 12).
"""

from __future__ import annotations

from typing import NoReturn

from phanes.experience_contracts import (
    BROKEN_EXPERIENCE_REFERENCE,
    DUPLICATE_EXPERIENCE_ID,
    EXPERIENCE_REFERENCE_CYCLE,
    INVALID_EXPERIENCE,
    KIND_BODY_PARAMETER_OBSERVATION,
    KIND_PLACE_OBSERVATION,
    MODE_EXACT,
    MODE_INTERVAL,
    MODE_NONE,
    MODE_UNKNOWN,
    SUPERSESSION_SUBJECT_MISMATCH,
)
from phanes.experience_validation import (
    ExperienceValidationError,
    validate_experience_document_structure,
)

# The kind-by-dependency authority table (Freeze section 8.2): the only
# rule determining whether a kind-specific dependency combination is valid.
_KIND_DEPENDENCY_TABLE = {
    KIND_PLACE_OBSERVATION: {
        "body": {MODE_NONE, MODE_EXACT},
        "environment": {MODE_EXACT},
        "frame": {MODE_EXACT},
        "temporal": {MODE_NONE, MODE_INTERVAL, MODE_UNKNOWN},
    },
    KIND_BODY_PARAMETER_OBSERVATION: {
        "body": {MODE_EXACT},
        "environment": {MODE_NONE},
        "frame": {MODE_NONE},
        "temporal": {MODE_NONE},
    },
}

_DEPENDENCY_DIMENSIONS = ("body", "environment", "frame", "temporal")


def validate_experience_document(document: object, agent_id: str) -> None:
    """Full structural + semantic validation of an Experience v1 document
    against the given Identity agent_id (Freeze section 10).

    Raises ExperienceValidationError on any violation; nothing is mutated.
    """
    validate_experience_document_structure(document)
    _validate_experience_document_semantics(document, agent_id)


def _validate_experience_document_semantics(document: dict, agent_id: str) -> None:
    """Semantic validation of an already structurally valid document.

    Callers must run validate_experience_document_structure first (or use
    validate_experience_document, which composes both layers).
    """
    records = document["records"]

    # --- Agent consistency: Identity == document == every record (§6) ---
    if document["agent_id"] != agent_id:
        _fail(INVALID_EXPERIENCE, "$.agent_id", "document agent_id does not match identity agent_id")
    for index, record in enumerate(records):
        path = f"$.records[{index}]"
        if record["agent_id"] != agent_id:
            _fail(INVALID_EXPERIENCE, f"{path}.agent_id", "record agent_id does not match identity agent_id")

    by_id = {}
    for index, record in enumerate(records):
        path = f"$.records[{index}]"
        experience_id = record["experience_id"]
        if experience_id in by_id:
            _fail(DUPLICATE_EXPERIENCE_ID, f"{path}.experience_id", f"duplicate experience_id: {experience_id}")
        by_id[experience_id] = record

    # --- Kind-by-dependency authority table (§8.2) and temporal ordering (§8.3) ---
    for index, record in enumerate(records):
        path = f"$.records[{index}]"
        _validate_kind_dependencies(record, path)
        _validate_temporal_ordering(record, path)

    # --- Reference existence and integrity (§13 / invariant 16) ---
    for index, record in enumerate(records):
        path = f"$.records[{index}]"
        parents = record["derived_from"]
        if len(set(parents)) != len(parents):
            _fail(INVALID_EXPERIENCE, f"{path}.derived_from", "derived_from contains duplicate parent ids")
        for parent in parents:
            if parent == record["experience_id"]:
                _fail(EXPERIENCE_REFERENCE_CYCLE, f"{path}.derived_from", "self-reference")
            if parent not in by_id:
                _fail(
                    BROKEN_EXPERIENCE_REFERENCE,
                    f"{path}.derived_from",
                    f"referenced parent does not exist in this document: {parent}",
                )
        supersedes = record["supersedes"]
        if supersedes == record["experience_id"]:
            _fail(EXPERIENCE_REFERENCE_CYCLE, f"{path}.supersedes", "self-reference")
        if supersedes is not None and supersedes not in by_id:
            _fail(
                BROKEN_EXPERIENCE_REFERENCE,
                f"{path}.supersedes",
                f"superseded record does not exist in this document: {supersedes}",
            )

    # A mixed derivation/supersession cycle is a cycle in the same ancestry.
    _check_acyclic(records)

    # --- Supersession: subject identity and non-branching (§13) ---
    succeeded = set()
    for index, record in enumerate(records):
        path = f"$.records[{index}]"
        supersedes = record["supersedes"]
        if supersedes is None:
            continue
        parent = by_id[supersedes]
        if record["kind"] != parent["kind"]:
            _fail(
                SUPERSESSION_SUBJECT_MISMATCH,
                f"{path}.supersedes",
                "supersession must preserve the experience kind",
            )
        if _subject_identity(record) != _subject_identity(parent):
            _fail(
                SUPERSESSION_SUBJECT_MISMATCH,
                f"{path}.supersedes",
                "supersession must preserve the subject identity of the record",
            )
        if supersedes in succeeded:
            _fail(
                INVALID_EXPERIENCE,
                f"{path}.supersedes",
                f"record {supersedes} has more than one direct successor (branching)",
            )
        succeeded.add(supersedes)


# --- Individual rules ---


def _validate_kind_dependencies(record: dict, path: str) -> None:
    kind = record["kind"]
    allowed = _KIND_DEPENDENCY_TABLE[kind]
    dependencies = record["dependencies"]
    for dimension in _DEPENDENCY_DIMENSIONS:
        mode = dependencies[dimension]["mode"]
        if mode not in allowed[dimension]:
            _fail(
                INVALID_EXPERIENCE,
                f"{path}.dependencies.{dimension}",
                f"{kind} does not allow {dimension} dependency mode {mode}",
            )


def _validate_temporal_ordering(record: dict, path: str) -> None:
    temporal = record["dependencies"]["temporal"]
    if temporal["mode"] != MODE_INTERVAL:
        return
    # Structural validation guarantees fixed-width UTC date/time fields.
    # Compare fractional digits exactly: datetime.fromisoformat truncates
    # beyond microseconds, while P1.1 permits arbitrary fractional precision.
    valid_from = _utc_order_key(temporal["valid_from"])
    valid_until = _utc_order_key(temporal["valid_until"])
    if not valid_from < valid_until:
        _fail(
            INVALID_EXPERIENCE,
            f"{path}.dependencies.temporal",
            "INTERVAL requires valid_from < valid_until",
        )


def _utc_order_key(value: str) -> tuple[str, str]:
    date_and_second = value[:19]
    fraction = ""
    if value[19] == ".":
        fraction = value[20:].split("Z", 1)[0].split("+", 1)[0]
    return date_and_second, fraction.rstrip("0")


def _subject_identity(record: dict) -> tuple:
    """Frozen supersession subject identity (Freeze section 13)."""
    kind = record["kind"]
    if kind == KIND_PLACE_OBSERVATION:
        return (kind, record["payload"]["place_id"])
    # body_parameter_observation: the kind table guarantees body is EXACT,
    # so the body instance identifier is present.
    payload = record["payload"]
    return (
        kind,
        record["dependencies"]["body"]["id"],
        payload["parameter_name"],
        payload["unit"],
    )


def _check_acyclic(records: list[dict]) -> None:
    """Detect cycles in the combined child-to-parent graph without recursion."""
    edges = {}
    for record in records:
        experience_id = record["experience_id"]
        parents = list(record["derived_from"])
        if record["supersedes"] is not None:
            parents.append(record["supersedes"])
        edges[experience_id] = parents

    white, gray, black = 0, 1, 2
    color = {experience_id: white for experience_id in edges}
    for start in edges:
        if color[start] != white:
            continue
        color[start] = gray
        stack = [(start, iter(edges[start]))]
        while stack:
            node, successors = stack[-1]
            descended = False
            for nxt in successors:
                if color[nxt] == gray:
                    _fail(
                        EXPERIENCE_REFERENCE_CYCLE,
                        "relations",
                        f"experience relations form a cycle involving {nxt}",
                    )
                if color[nxt] == white:
                    color[nxt] = gray
                    stack.append((nxt, iter(edges[nxt])))
                    descended = True
                    break
            if not descended:
                color[node] = black
                stack.pop()


def _fail(code: str, path: str, message: str) -> NoReturn:
    raise ExperienceValidationError(code, f"{path}: {message}")
