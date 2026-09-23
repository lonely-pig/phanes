"""PhanesCore: deterministic mapping from the fixed P0 command syntax to
memory operations or capability invocations (Architecture Freeze sections
3 and 12).

Core depends only on contracts, Identity, MemoryStore and the injected
CapabilityRegistry. It never imports discovery, adapters or phanes_host,
never reads host configuration and never grants authority. Every body
action follows lookup -> registry.list -> registry.invoke; there is no
path from Core to an adapter.
"""

from __future__ import annotations

import re

from phanes.capabilities import CapabilityRegistry
from phanes.contracts import (
    CAPABILITY_UNAVAILABLE,
    INVALID_COMMAND,
    MOVE_TO,
    NO_BODY,
    UNKNOWN_PLACE,
    BodyDescriptor,
    CapabilitySpec,
    Identity,
    InvocationResult,
    is_valid_coordinate,
)
from phanes.memory import MemoryStore

# Fixed command syntax (Freeze section 12). Commas and parentheses accept
# Chinese and English forms; separator whitespace and a trailing 。 are
# optional. No free-form understanding is provided.
_REMEMBER_RE = re.compile(
    r"^记住\s*(\S+?)\s*是坐标\s*[（(]\s*"
    r"([^,，()（）]+?)\s*[,，]\s*([^,，()（）]+?)\s*"
    r"[)）]\s*。?$"
)
_QUERY_RE = re.compile(r"^查询\s*(\S+?)\s*。?$")
_MOVE_RE = re.compile(r"^去\s*(\S+?)\s*。?$")

# Place tokens must not contain whitespace or syntax delimiters (Freeze §6).
_PLACE_FORBIDDEN_CHARS = frozenset("（）(),，.。")


def _is_valid_place_token(token: str) -> bool:
    return bool(token) and not any(
        ch.isspace() or ch in _PLACE_FORBIDDEN_CHARS for ch in token
    )


def _parse_number(text: str) -> float | int | None:
    """Parse a CLI coordinate token; non-finite values are rejected by the
    caller via is_valid_coordinate."""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None


class PhanesCore:
    """Maps fixed commands to memory operations or capability invocations."""

    def __init__(
        self,
        identity: Identity,
        memory: MemoryStore,
        registry: CapabilityRegistry,
    ) -> None:
        self._identity = identity
        self._memory = memory
        self._registry = registry

    @property
    def identity(self) -> Identity:
        return self._identity

    @property
    def current_body(self) -> BodyDescriptor | None:
        return self._registry.current_body

    @property
    def capabilities(self) -> tuple[CapabilitySpec, ...]:
        """The authorized capability view; never grants anything."""
        return self._registry.list()

    def handle_command(self, text: str) -> InvocationResult:
        """Handle one fixed-syntax command line. Unknown syntax is
        INVALID_COMMAND; unknown places are UNKNOWN_PLACE."""
        line = text.strip() if isinstance(text, str) else ""
        match = _REMEMBER_RE.match(line)
        if match:
            return self._handle_remember(match.group(1), match.group(2), match.group(3))
        match = _QUERY_RE.match(line)
        if match:
            return self._handle_query(match.group(1))
        match = _MOVE_RE.match(line)
        if match:
            return self._handle_move(match.group(1))
        return InvocationResult.failure(INVALID_COMMAND, f"unsupported command: {line!r}")

    def _handle_remember(self, name: str, x_text: str, y_text: str) -> InvocationResult:
        if not _is_valid_place_token(name):
            return InvocationResult.failure(INVALID_COMMAND, f"invalid place name: {name!r}")
        x = _parse_number(x_text)
        y = _parse_number(y_text)
        if x is None or y is None or not is_valid_coordinate(x) or not is_valid_coordinate(y):
            return InvocationResult.failure(
                INVALID_COMMAND, f"invalid coordinates: {x_text!r}, {y_text!r}"
            )
        try:
            self._memory.remember_place(name, x, y)
        except ValueError as exc:
            return InvocationResult.failure(INVALID_COMMAND, str(exc))
        return InvocationResult.success({"action": "remember", "place": name, "x": x, "y": y})

    def _handle_query(self, name: str) -> InvocationResult:
        if not _is_valid_place_token(name):
            return InvocationResult.failure(INVALID_COMMAND, f"invalid place name: {name!r}")
        position = self._memory.lookup_place(name)
        if position is None:
            return InvocationResult.failure(UNKNOWN_PLACE, f"unknown place: {name}")
        return InvocationResult.success(
            {"action": "query", "place": name, "x": position.x, "y": position.y}
        )

    def _handle_move(self, name: str) -> InvocationResult:
        if not _is_valid_place_token(name):
            return InvocationResult.failure(INVALID_COMMAND, f"invalid place name: {name!r}")
        position = self._memory.lookup_place(name)
        if position is None:
            return InvocationResult.failure(UNKNOWN_PLACE, f"unknown place: {name}")
        if self._registry.current_body is None:
            return InvocationResult.failure(NO_BODY, "no body is bound")
        if MOVE_TO not in {spec.name for spec in self._registry.list()}:
            return InvocationResult.failure(CAPABILITY_UNAVAILABLE, "move_to is not registered")
        return self._registry.invoke(MOVE_TO, {"x": position.x, "y": position.y})
