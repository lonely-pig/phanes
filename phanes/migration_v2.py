"""Trusted, offline P1 package-v2 transport of complete Self and generic Runtime.

Package source files are copied and hashed as data. Validation and import
never load or execute them, and never construct a Body or authority.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from phanes.contracts import ADAPTER_API_VERSION, PYTHON_REQUIRES
from phanes.experience_store import EXPERIENCE_FILENAME, ExperienceStore, ExperienceStoreError
from phanes.experience_validation import ExperienceValidationError
from phanes.identity import IDENTITY_FILENAME, IdentityError, load_identity
from phanes.memory import MEMORY_FILENAME, MemoryStore, MemoryStoreError
from phanes.migration import MigrationError, _scan_package, _sha256, _validate_relative_path

PACKAGE_VERSION_V2 = 2
P1_CORE_VERSION = "0.2"
MANIFEST_FILENAME = "manifest.json"
SELF_FILES_V2 = (IDENTITY_FILENAME, MEMORY_FILENAME, EXPERIENCE_FILENAME)

# Closed transitive import set for p1_runtime. In particular, no P0 CLI,
# legacy command Core, discovery, migration tool, or Host adapter is carried.
RUNTIME_FILES_V2 = (
    "__init__.py",
    "contracts.py",
    "capabilities.py",
    "identity.py",
    "memory.py",
    "experience_contracts.py",
    "experience_validation.py",
    "experience_semantic.py",
    "experience_store.py",
    "applicability.py",
    "experience_gateway.py",
    "experience_core.py",
    "p1_runtime.py",
)

_MANIFEST_KEYS = frozenset(
    {"package_version", "core_version", "python_requires", "adapter_api_version", "agent_id", "files"}
)
_EXPECTED_DIRS = frozenset({"self", "runtime", "runtime/phanes"})
_HASH_RE = re.compile(r"[0-9a-f]{64}")


class MigrationV2Error(Exception):
    """A package-v2 validation or transport failure; never a fallback signal."""


def _package_files() -> set[str]:
    return {f"self/{name}" for name in SELF_FILES_V2} | {
        f"runtime/phanes/{name}" for name in RUNTIME_FILES_V2
    }


def _require_disjoint_roots(source: Path, target: Path) -> None:
    """Staging must never appear inside the input or replace its ancestor."""
    source_root, target_root = source.resolve(), target.resolve()
    if source_root == target_root or source_root in target_root.parents or target_root in source_root.parents:
        raise MigrationV2Error("source and target directories must be disjoint")


def _validate_complete_self(state_dir: Path, *, exact_files: bool) -> str:
    """Return validated agent ID. A missing Experience is never an empty log."""
    if state_dir.is_symlink() or not state_dir.is_dir():
        raise MigrationV2Error(f"Self directory not found or is a symlink: {state_dir}")
    if exact_files:
        entries = {entry.name: entry for entry in state_dir.iterdir()}
        if set(entries) != set(SELF_FILES_V2):
            raise MigrationV2Error(
                f"Self files must be exactly {sorted(SELF_FILES_V2)}, got {sorted(entries)}"
            )
        if any(entry.is_symlink() or not entry.is_file() for entry in entries.values()):
            raise MigrationV2Error("Self files must be regular files, not symlinks")
    try:
        identity = load_identity(state_dir)
        MemoryStore.load(state_dir, identity.agent_id)
        ExperienceStore.load(state_dir, identity.agent_id)
    except (IdentityError, MemoryStoreError, ExperienceStoreError, ExperienceValidationError) as exc:
        raise MigrationV2Error(f"invalid complete Self: {exc}") from exc
    return identity.agent_id


def validate_package_v2(package_dir: Path | str) -> dict:
    """Validate the exact v2 package as inert data; never import package code."""
    package_dir = Path(package_dir)
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise MigrationV2Error(f"package directory not found or is a symlink: {package_dir}")
    manifest_path = package_dir / MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise MigrationV2Error(f"missing or symlinked manifest: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationV2Error(f"corrupt manifest: {exc}") from exc
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS:
        raise MigrationV2Error("manifest has missing or extra fields")
    if type(manifest["package_version"]) is not int or manifest["package_version"] != PACKAGE_VERSION_V2:
        raise MigrationV2Error(f"unsupported package_version: {manifest['package_version']!r}")
    if manifest["core_version"] != P1_CORE_VERSION:
        raise MigrationV2Error(f"unsupported core_version: {manifest['core_version']!r}")
    if manifest["python_requires"] != PYTHON_REQUIRES:
        raise MigrationV2Error(f"unsupported python_requires: {manifest['python_requires']!r}")
    if type(manifest["adapter_api_version"]) is not int or manifest["adapter_api_version"] != ADAPTER_API_VERSION:
        raise MigrationV2Error(f"unsupported adapter_api_version: {manifest['adapter_api_version']!r}")
    agent_id = manifest["agent_id"]
    if not isinstance(agent_id, str):
        raise MigrationV2Error("manifest agent_id must be a string")
    try:
        parsed = uuid.UUID(agent_id)
    except ValueError as exc:
        raise MigrationV2Error("manifest agent_id is not a UUID") from exc
    if parsed.version != 4 or str(parsed) != agent_id:
        raise MigrationV2Error("manifest agent_id must be canonical uuid4")
    files = manifest["files"]
    expected = _package_files()
    if type(files) is not dict or set(files) != expected:
        raise MigrationV2Error("manifest file whitelist mismatch")
    for rel, digest in files.items():
        try:
            _validate_relative_path(rel)
        except MigrationError as exc:
            raise MigrationV2Error(str(exc)) from exc
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            raise MigrationV2Error(f"invalid SHA-256 for {rel!r}")
    try:
        on_disk_files, on_disk_dirs = _scan_package(package_dir)
    except (MigrationError, OSError) as exc:
        raise MigrationV2Error(f"invalid package tree: {exc}") from exc
    if on_disk_dirs != _EXPECTED_DIRS or on_disk_files != expected | {MANIFEST_FILENAME}:
        raise MigrationV2Error("package tree differs from fixed whitelist")
    for rel, digest in files.items():
        try:
            actual = _sha256(package_dir / Path(rel))
        except OSError as exc:
            raise MigrationV2Error(f"cannot hash {rel!r}: {exc}") from exc
        if actual != digest:
            raise MigrationV2Error(f"SHA-256 mismatch for {rel!r}")
    actual_agent_id = _validate_complete_self(package_dir / "self", exact_files=True)
    if actual_agent_id != agent_id:
        raise MigrationV2Error("manifest agent_id does not match complete Self")
    return manifest


def export_package_v2(state_dir: Path | str, out_dir: Path | str) -> Path:
    """Validate Self, stage a fixed package, validate it, then commit by rename."""
    state_dir, out_dir = Path(state_dir), Path(out_dir)
    _require_disjoint_roots(state_dir, out_dir)
    agent_id = _validate_complete_self(state_dir, exact_files=True)
    if out_dir.exists() or out_dir.is_symlink():
        raise MigrationV2Error(f"package target already exists: {out_dir}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=out_dir.parent, prefix=out_dir.name + ".tmp-"))
    try:
        self_dir = tmp_dir / "self"
        self_dir.mkdir()
        runtime_dir = tmp_dir / "runtime" / "phanes"
        runtime_dir.mkdir(parents=True)
        for name in SELF_FILES_V2:
            shutil.copy2(state_dir / name, self_dir / name)
        runtime_src = Path(__file__).resolve().parent
        for name in RUNTIME_FILES_V2:
            shutil.copy2(runtime_src / name, runtime_dir / name)
        files = {rel: _sha256(tmp_dir / Path(rel)) for rel in sorted(_package_files())}
        manifest = {
            "package_version": PACKAGE_VERSION_V2,
            "core_version": P1_CORE_VERSION,
            "python_requires": PYTHON_REQUIRES,
            "adapter_api_version": ADAPTER_API_VERSION,
            "agent_id": agent_id,
            "files": files,
        }
        (tmp_dir / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validate_package_v2(tmp_dir)
        if out_dir.exists() or out_dir.is_symlink():
            raise MigrationV2Error(f"package target already exists: {out_dir}")
        tmp_dir.rename(out_dir)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return out_dir


def import_package_v2(package_dir: Path | str, state_dir: Path | str):
    """Import Self only, atomically; package code is never executed."""
    package_dir, state_dir = Path(package_dir), Path(state_dir)
    _require_disjoint_roots(package_dir, state_dir)
    if state_dir.exists() or state_dir.is_symlink():
        raise MigrationV2Error(f"Self target already exists: {state_dir}")
    manifest = validate_package_v2(package_dir)
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=state_dir.parent, prefix=state_dir.name + ".tmp-"))
    try:
        for name in SELF_FILES_V2:
            shutil.copy2(package_dir / "self" / name, tmp_dir / name)
            if _sha256(tmp_dir / name) != manifest["files"][f"self/{name}"]:
                raise MigrationV2Error(f"staged Self hash changed during import: {name}")
        _validate_complete_self(tmp_dir, exact_files=True)
        identity = load_identity(tmp_dir)
        if state_dir.exists() or state_dir.is_symlink():
            raise MigrationV2Error(f"Self target already exists: {state_dir}")
        tmp_dir.rename(state_dir)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return identity
