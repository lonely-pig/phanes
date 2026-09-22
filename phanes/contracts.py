"""Shared contracts for Phanes P0 (Architecture Freeze v0.1).

Defines the fixed data structures, version constants, error codes, the
BodyAdapter Protocol and the fixed capability argument/result validation
that all later modules depend on (Freeze sections 5, 6, 7, 8).

This module uses only the Python standard library and must not import
discovery, concrete adapters, phanes_host, OS/hardware APIs or anything
outside the standard library.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# --- Version constants (Freeze: versions are format metadata, not authority) ---
IDENTITY_SCHEMA_VERSION = 1
MEMORY_SCHEMA_VERSION = 1
ADAPTER_API_VERSION = 1
CAPABILITY_CONTRACT_VERSION = 1
PACKAGE_VERSION = 1
PYTHON_REQUIRES = ">=3.11"

# --- Error codes (Freeze section 8) ---
NO_BODY = "NO_BODY"
CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
ADAPTER_ERROR = "ADAPTER_ERROR"
INVALID_RESULT = "INVALID_RESULT"
# Core-level codes (command parsing / memory lookup):
UNKNOWN_PLACE = "UNKNOWN_PLACE"
INVALID_COMMAND = "INVALID_COMMAND"

# --- Fixed capability names (Freeze section 8 contract table) ---
GET_POSITION = "get_position"
MOVE_TO = "move_to"


class ContractError(ValueError):
    """Raised when capability arguments or results violate the fixed contract."""


@dataclass(frozen=True)
class Position:
    """A 2D local mock coordinate (Freeze section 7)."""

    x: float | int
    y: float | int


@dataclass(frozen=True)
class Identity:
    """Persistent identity fields (Freeze section 5). JSON I/O lives in identity.py."""

    agent_id: str
    name: str
    created_at: str
    schema_version: int = IDENTITY_SCHEMA_VERSION


@dataclass(frozen=True)
class BodyDescriptor:
    """Read-only description of the currently bound body (Freeze section 7)."""

    body_id: str
    body_type: str
    adapter_api_version: int


@dataclass(frozen=True)
class CapabilitySpec:
    """One offered capability (Freeze section 8)."""

    name: str
    contract_version: int = CAPABILITY_CONTRACT_VERSION


@dataclass(frozen=True)
class InvocationError:
    code: str
    message: str


@dataclass(frozen=True)
class InvocationResult:
    """Structured outcome of CapabilityRegistry.invoke (Freeze section 8)."""

    ok: bool
    data: dict[str, Any] | None = None
    error: InvocationError | None = None

    @classmethod
    def success(cls, data: dict[str, Any]) -> InvocationResult:
        return cls(ok=True, data=data, error=None)

    @classmethod
    def failure(cls, code: str, message: str) -> InvocationResult:
        return cls(ok=False, data=None, error=InvocationError(code=code, message=message))


@runtime_checkable
class BodyAdapter(Protocol):
    """The single Protocol every body adapter implements (Freeze section 8)."""

    @property
    def descriptor(self) -> BodyDescriptor: ...

    def capabilities(self) -> tuple[CapabilitySpec, ...]: ...

    def invoke(self, name: str, args: dict[str, Any]) -> dict[str, Any]: ...


# --- Fixed capability contract validation (Freeze section 8) ---
# Explicit validation functions; no generic JSON Schema engine.

_ARG_KEYS: dict[str, frozenset[str]] = {
    GET_POSITION: frozenset(),
    MOVE_TO: frozenset({"x", "y"}),
}

# Both v1 capabilities return the current position on success.
_RESULT_KEYS = frozenset({"x", "y"})

KNOWN_CAPABILITIES = frozenset(_ARG_KEYS)


def is_valid_coordinate(value: object) -> bool:
    """Finite JSON number; rejects bool, NaN and Infinity (Freeze section 6)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def validate_capability_args(name: str, args: object) -> None:
    """Validate invoke arguments against the fixed v1 contract.

    Keys must match the contract exactly: missing or extra keys are rejected.
    Raises ContractError on any violation.
    """
    if name not in _ARG_KEYS:
        raise ContractError(f"unknown capability: {name!r}")
    if not isinstance(args, dict):
        raise ContractError(f"{name} args must be a dict")
    expected = _ARG_KEYS[name]
    keys = set(args)
    missing = expected - keys
    extra = keys - expected
    if missing:
        raise ContractError(f"{name} missing argument(s): {sorted(missing)}")
    if extra:
        raise ContractError(f"{name} unexpected argument(s): {sorted(extra)}")
    for key in expected:
        if not is_valid_coordinate(args[key]):
            raise ContractError(f"{name} argument {key!r} is not a finite number: {args[key]!r}")


def validate_capability_result(name: str, data: object) -> None:
    """Validate a successful adapter result against the fixed v1 contract.

    Success data for both P0 capabilities is exactly the current position.
    Raises ContractError on any violation.
    """
    if name not in _ARG_KEYS:
        raise ContractError(f"unknown capability: {name!r}")
    if not isinstance(data, dict):
        raise ContractError(f"{name} result must be a dict")
    keys = set(data)
    if keys != _RESULT_KEYS:
        raise ContractError(f"{name} result keys must be exactly {sorted(_RESULT_KEYS)}, got {sorted(keys)}")
    for key in _RESULT_KEYS:
        if not is_valid_coordinate(data[key]):
            raise ContractError(f"{name} result {key!r} is not a finite number: {data[key]!r}")
