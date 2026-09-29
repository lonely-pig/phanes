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
    SOURCE_CLASS_TEST_FIXTURE,
)
from phanes.experience_semantic import validate_experience_document
from phanes.experience_validation import ExperienceValidationError
from phanes.identity import atomic_write_json, load_identity

EXPERIENCE_FILENAME = "experiences.json"
EXPERIENCES_FILENAME = EXPERIENCE_FILENAME


class ExperienceStoreError(Exception):
    """Missing, corrupt, or inaccessible Experience persistence."""


class ExperienceStore:
    """Identity-bound Experience history; local append is test-fixture-only in P1."""

    def __init__(self, state_dir: Path | str, identity_agent_id: str, *, test_mode: bool = False) -> None:
        if type(test_mode) is not bool:
            raise TypeError("test_mode must be bool")
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
        self._test_mode = test_mode

    @classmethod
    def create(cls, state_dir: Path | str, agent_id: str, *, test_mode: bool = False) -> ExperienceStore:
        if type(test_mode) is not bool:
            raise TypeError("test_mode must be bool")
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
        return cls(state_dir, agent_id, test_mode=test_mode)

    @classmethod
    def load(cls, state_dir: Path | str, identity_agent_id: str, *, test_mode: bool = False) -> ExperienceStore:
        return cls(state_dir, identity_agent_id, test_mode=test_mode)

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
        source_class = candidate["records"][-1]["provenance"]["source_class"]
        if not self._test_mode or source_class != SOURCE_CLASS_TEST_FIXTURE:
            raise ExperienceStoreError(
                "local Experience creation requires explicit test mode and test_fixture provenance"
            )
        atomic_write_json(self._path, candidate)
        self._document = candidate
