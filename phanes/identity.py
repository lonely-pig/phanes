"""Identity persistence (Architecture Freeze section 5).

Identity is created exactly once via init_identity() on an empty state
directory. Loading is strict: a missing or corrupt identity file is an
error, never a reason to generate a replacement identity.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from phanes.contracts import IDENTITY_SCHEMA_VERSION, Identity

AGENT_NAME = "Phanes"
IDENTITY_FILENAME = "identity.json"

_IDENTITY_KEYS = {"schema_version", "agent_id", "name", "created_at"}


class IdentityError(Exception):
    """Identity init/load failure. Never silently recover or regenerate."""


def atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON via same-directory temp file, flush, fsync, os.replace.

    On failure the previous valid file (if any) is preserved and the temp
    file is removed (Freeze section 6 write rules, shared by Self files).
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def init_identity(state_dir: Path | str) -> Identity:
    """Create a new identity in an empty state directory.

    Refuses to overwrite any existing data (Freeze section 5: init is only
    allowed on an empty state directory).
    """
    state_dir = Path(state_dir)
    if state_dir.exists():
        if not state_dir.is_dir():
            raise IdentityError(f"state path is not a directory: {state_dir}")
        if any(state_dir.iterdir()):
            raise IdentityError(f"state directory is not empty, refusing to initialize: {state_dir}")
    else:
        state_dir.mkdir(parents=True)

    identity = Identity(
        agent_id=str(uuid.uuid4()),
        name=AGENT_NAME,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    atomic_write_json(
        state_dir / IDENTITY_FILENAME,
        {
            "schema_version": IDENTITY_SCHEMA_VERSION,
            "agent_id": identity.agent_id,
            "name": identity.name,
            "created_at": identity.created_at,
        },
    )
    return identity


def load_identity(state_dir: Path | str) -> Identity:
    """Load and strictly validate the identity file. Corruption is fatal."""
    path = Path(state_dir) / IDENTITY_FILENAME
    if not path.is_file():
        raise IdentityError(f"missing identity file: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityError(f"corrupt identity file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise IdentityError(f"identity file must contain a JSON object: {path}")

    keys = set(raw)
    if keys != _IDENTITY_KEYS:
        raise IdentityError(f"identity fields must be exactly {sorted(_IDENTITY_KEYS)}, got {sorted(keys)}")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != IDENTITY_SCHEMA_VERSION:
        raise IdentityError(f"unsupported identity schema_version: {raw['schema_version']!r}")
    if not isinstance(raw["agent_id"], str):
        raise IdentityError("identity agent_id must be a string")
    try:
        parsed_uuid = uuid.UUID(raw["agent_id"])
    except ValueError as exc:
        raise IdentityError(f"identity agent_id is not a valid UUID: {raw['agent_id']!r}") from exc
    if parsed_uuid.version != 4 or str(parsed_uuid) != raw["agent_id"]:
        raise IdentityError(
            f"identity agent_id must be a canonical uuid4 string: {raw['agent_id']!r}"
        )
    if raw["name"] != AGENT_NAME:
        raise IdentityError(f"identity name must be {AGENT_NAME!r}, got {raw['name']!r}")
    if not isinstance(raw["created_at"], str) or not raw["created_at"].endswith("Z"):
        raise IdentityError("identity created_at must be UTC ISO 8601 with a 'Z' suffix")
    try:
        parsed_created_at = datetime.fromisoformat(raw["created_at"])
    except ValueError as exc:
        raise IdentityError(f"identity created_at is not ISO 8601: {raw['created_at']!r}") from exc
    if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() != timedelta(0):
        raise IdentityError("identity created_at must be timezone-aware UTC")

    return Identity(
        agent_id=raw["agent_id"],
        name=raw["name"],
        created_at=raw["created_at"],
        schema_version=raw["schema_version"],
    )
