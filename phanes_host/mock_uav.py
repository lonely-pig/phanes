"""MockUAV: the first P0 body (Architecture Freeze sections 7-8).

A host-side adapter, never imported by the generic runtime. Simulates a
2D position in mock units; no flight dynamics, no timing, no hardware.
"""

from __future__ import annotations

from typing import Any

from phanes.contracts import (
    ADAPTER_API_VERSION,
    GET_POSITION,
    MOVE_TO,
    BodyDescriptor,
    CapabilitySpec,
    ContractError,
    validate_capability_args,
)


def _display(value: float | int) -> str:
    """Integer coordinates display as integers (Freeze section 8 rule 6)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class MockUAVAdapter:
    """Implements the BodyAdapter protocol with get_position and move_to v1."""

    def __init__(self, body_id: str) -> None:
        self._descriptor = BodyDescriptor(
            body_id=body_id,
            body_type="mock_uav",
            adapter_api_version=ADAPTER_API_VERSION,
        )
        self._x: float | int = 0
        self._y: float | int = 0

    @property
    def descriptor(self) -> BodyDescriptor:
        return self._descriptor

    def capabilities(self) -> tuple[CapabilitySpec, ...]:
        return (CapabilitySpec(GET_POSITION), CapabilitySpec(MOVE_TO))

    def invoke(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == GET_POSITION:
            validate_capability_args(name, args)
            return {"x": self._x, "y": self._y}
        if name == MOVE_TO:
            validate_capability_args(name, args)
            x, y = args["x"], args["y"]
            print(f"[MockUAV] moving to ({_display(x)}, {_display(y)})")
            self._x, self._y = x, y
            return {"x": self._x, "y": self._y}
        raise ContractError(f"unknown capability: {name!r}")
