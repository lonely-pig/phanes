"""P1.2 append-only Experience persistence for a single process.

The file is a separate part of Self. Every loaded or appended document is
structurally and semantically validated against the existing Identity.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from phanes.experience_contracts import (
    EXPERIENCE_NOT_FOUND,
    EXPERIENCE_SCHEMA_VERSION,
    INVALID_EXPERIENCE,
)
from phanes.experience_semantic import validate_experience_document
from phanes.experience_validation import ExperienceValidationError
from phanes.identity import atomic_write_json, load_identity

EXPERIENCE_FILENAME = "experiences.json"
EXPERIENCES_FILENAME = EXPERIENCE_FILENAME


class ExperienceStoreError(Exception):
    """Missing, corrupt, or inaccessible Experience persistence."""


class ExperienceStore:
    """An Identity-bound, append-only Experience document."""

    def __init__(self, state_dir: Path | str, identity_agent_id: str) -> None:
        state_dir = Path(state_dir)
        if load_identity(state_dir).agent_id != identity_agent_id:
            raise ExperienceValidationError(INVALID_EXPERIENCE, "agent_id does not match Identity")
        path = state_dir / EXPERIENCE_FILENAME
        if not path.is_file():
            raise ExperienceStoreError(f"missing experience file: {path}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExperienceStoreError(f"corrupt experience file {path}: {exc}") from exc
        validate_experience_document(document, identity_agent_id)
        self._path = path
        self._agent_id = identity_agent_id
        self._document = copy.deepcopy(document)

    @classmethod
    def create(cls, state_dir: Path | str, agent_id: str) -> ExperienceStore:
        identity = load_identity(state_dir)
        if identity.agent_id != agent_id:
            raise ExperienceValidationError(INVALID_EXPERIENCE, "agent_id does not match Identity")
        path = Path(state_dir) / EXPERIENCE_FILENAME
        if path.exists():
            raise ExperienceStoreError(f"experience file already exists: {path}")
        document = {
            "schema_version": EXPERIENCE_SCHEMA_VERSION,
            "agent_id": agent_id,
            "records": [],
        }
        validate_experience_document(document, agent_id)
        atomic_write_json(path, document)
        return cls(state_dir, agent_id)

    @classmethod
    def load(cls, state_dir: Path | str, identity_agent_id: str) -> ExperienceStore:
        return cls(state_dir, identity_agent_id)

    def document_snapshot(self) -> dict:
        return copy.deepcopy(self._document)

    def get_record(self, experience_id: str) -> dict:
        for record in self._document["records"]:
            if record["experience_id"] == experience_id:
                return copy.deepcopy(record)
        raise ExperienceValidationError(EXPERIENCE_NOT_FOUND, f"experience_id not found: {experience_id!r}")

    def append_record(self, record: object) -> None:
        candidate = copy.deepcopy(self._document)
        candidate["records"].append(copy.deepcopy(record))
        validate_experience_document(candidate, self._agent_id)
        atomic_write_json(self._path, candidate)
        self._document = candidate
