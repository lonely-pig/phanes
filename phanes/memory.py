"""Place memory persistence (Architecture Freeze section 6).

Stores only place facts: name -> Position. No commands, capabilities,
credentials, grants or body state are ever written here. Loading is strict:
a corrupt memory file is an error, never treated as empty memory.
Single-process use only.
"""

from __future__ import annotations

import json
from pathlib import Path

from phanes.contracts import MEMORY_SCHEMA_VERSION, Position, is_valid_coordinate
from phanes.identity import atomic_write_json

MEMORY_FILENAME = "memory.json"

_MEMORY_KEYS = {"schema_version", "agent_id", "places"}
_PLACE_KEYS = {"x", "y"}


class MemoryStoreError(Exception):
    """Memory load/persistence failure. Corruption is never silently dropped."""


class MemoryStore:
    """Persistent place facts bound to one agent_id."""

    def __init__(self, state_dir: Path | str, agent_id: str, places: dict[str, Position]) -> None:
        self._path = Path(state_dir) / MEMORY_FILENAME
        self._agent_id = agent_id
        self._places = places

    @classmethod
    def create(cls, state_dir: Path | str, agent_id: str) -> MemoryStore:
        """Create an empty memory file for a new state directory."""
        path = Path(state_dir) / MEMORY_FILENAME
        if path.exists():
            raise MemoryStoreError(f"memory file already exists, refusing to overwrite: {path}")
        store = cls(state_dir, agent_id, {})
        store._persist()
        return store

    @classmethod
    def load(cls, state_dir: Path | str, agent_id: str) -> MemoryStore:
        """Load and fully validate the memory file.

        Validates JSON, schema version, field types and that memory.agent_id
        matches the given identity agent_id (Freeze section 6).
        """
        path = Path(state_dir) / MEMORY_FILENAME
        if not path.is_file():
            raise MemoryStoreError(f"missing memory file: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryStoreError(f"corrupt memory file {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise MemoryStoreError(f"memory file must contain a JSON object: {path}")

        keys = set(raw)
        if keys != _MEMORY_KEYS:
            raise MemoryStoreError(f"memory fields must be exactly {sorted(_MEMORY_KEYS)}, got {sorted(keys)}")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != MEMORY_SCHEMA_VERSION:
            raise MemoryStoreError(f"unsupported memory schema_version: {raw['schema_version']!r}")
        if not isinstance(raw["agent_id"], str):
            raise MemoryStoreError("memory agent_id must be a string")
        if raw["agent_id"] != agent_id:
            raise MemoryStoreError("memory agent_id does not match identity agent_id")
        if not isinstance(raw["places"], dict):
            raise MemoryStoreError("memory places must be an object")

        places: dict[str, Position] = {}
        for name, entry in raw["places"].items():
            if not isinstance(name, str) or not name or name != name.strip():
                raise MemoryStoreError(f"invalid place name in memory file: {name!r}")
            if not isinstance(entry, dict) or set(entry) != _PLACE_KEYS:
                raise MemoryStoreError(f"place {name!r} must have exactly keys {sorted(_PLACE_KEYS)}")
            if not is_valid_coordinate(entry["x"]) or not is_valid_coordinate(entry["y"]):
                raise MemoryStoreError(f"place {name!r} has non-finite or non-numeric coordinates")
            places[name] = Position(x=entry["x"], y=entry["y"])

        return cls(state_dir, agent_id, places)

    def remember_place(self, name: str, x: float | int, y: float | int) -> None:
        """Store or update a place. Success is reported only after the disk
        write succeeds; on failure the in-memory state is unchanged."""
        key = self._normalize_name(name)
        if not is_valid_coordinate(x) or not is_valid_coordinate(y):
            raise ValueError(f"coordinates must be finite numbers, got x={x!r}, y={y!r}")
        new_places = dict(self._places)
        new_places[key] = Position(x=x, y=y)
        self._persist(new_places)
        self._places = new_places

    def lookup_place(self, name: str) -> Position | None:
        """Return the stored position, or None for an unknown place. Never guesses."""
        return self._places.get(self._normalize_name(name))

    @staticmethod
    def _normalize_name(name: str) -> str:
        if not isinstance(name, str):
            raise ValueError(f"place name must be a string, got {type(name).__name__}")
        key = name.strip()
        if not key:
            raise ValueError("place name must be non-empty")
        return key

    def _persist(self, places: dict[str, Position] | None = None) -> None:
        if places is None:
            places = self._places
        atomic_write_json(
            self._path,
            {
                "schema_version": MEMORY_SCHEMA_VERSION,
                "agent_id": self._agent_id,
                "places": {name: {"x": pos.x, "y": pos.y} for name, pos in places.items()},
            },
        )
