"""Migration package export/import (Architecture Freeze section 10).

Migration is an offline, user-driven process: explicit export, manual
copy, explicit import, restart. The package is a plain directory with a
fixed structure containing only Self data and whitelisted generic runtime
source files. Authority (host config, grants), body adapters and runtime
body state are never part of the package. Import restores Self only; it
never discovers bodies, binds registries or executes anything from the
package. SHA-256 in the manifest proves integrity, not origin authenticity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath

import phanes
from phanes.contracts import ADAPTER_API_VERSION, PACKAGE_VERSION, PYTHON_REQUIRES, Identity
from phanes.identity import IDENTITY_FILENAME, IdentityError, load_identity
from phanes.memory import MEMORY_FILENAME, MemoryStore, MemoryStoreError

MANIFEST_FILENAME = "manifest.json"

# Fixed source whitelist (Freeze section 11). Never derived by walking the
# repository; nothing else may enter the package.
RUNTIME_FILES = (
    "__init__.py",
    "__main__.py",
    "contracts.py",
    "core.py",
    "identity.py",
    "memory.py",
    "capabilities.py",
    "discovery.py",
    "migration.py",
)
SELF_FILES = (IDENTITY_FILENAME, MEMORY_FILENAME)

_MANIFEST_KEYS = {
    "package_version",
    "core_version",
    "python_requires",
    "adapter_api_version",
    "agent_id",
    "files",
}
_EXPECTED_DIRS = {"self", "runtime", "runtime/phanes"}
_HASH_RE = re.compile(r"[0-9a-f]{64}")


class MigrationError(Exception):
    """Export/import validation or commit failure. Never leaves partial output."""


def _expected_package_files() -> set[str]:
    files = {f"self/{name}" for name in SELF_FILES}
    files.update(f"runtime/phanes/{name}" for name in RUNTIME_FILES)
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_relative_path(path: object) -> None:
    """Reject anything that is not a clean relative POSIX-style path."""
    if not isinstance(path, str) or not path:
        raise MigrationError(f"invalid package path: {path!r}")
    if "\\" in path:
        raise MigrationError(f"backslash is not allowed in package path: {path!r}")
    if re.match(r"^[A-Za-z]:", path):
        raise MigrationError(f"drive-absolute package path: {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise MigrationError(f"absolute package path: {path!r}")
    if not pure.parts or any(part in ("..", "") for part in pure.parts):
        raise MigrationError(f"path traversal in package path: {path!r}")
    if str(pure) != path:
        raise MigrationError(f"non-canonical package path: {path!r}")


def _scan_package(root: Path) -> tuple[set[str], set[str]]:
    """Collect relative POSIX paths of all files and dirs below root.

    Any symlink or non-regular file is rejected.
    """
    files: set[str] = set()
    dirs: set[str] = set()

    def walk(dir_path: Path, rel: str) -> None:
        with os.scandir(dir_path) as entries:
            for entry in entries:
                entry_rel = f"{rel}/{entry.name}" if rel else entry.name
                if entry.is_symlink():
                    raise MigrationError(f"symlink in package: {entry_rel}")
                if entry.is_dir(follow_symlinks=False):
                    dirs.add(entry_rel)
                    walk(Path(entry.path), entry_rel)
                elif entry.is_file(follow_symlinks=False):
                    files.add(entry_rel)
                else:
                    raise MigrationError(f"non-regular file in package: {entry_rel}")

    walk(root, "")
    return files, dirs


def validate_package(package_dir: Path | str) -> dict:
    """Fully validate a migration package without executing anything in it.

    The package runtime is data here: only JSON parsing and SHA-256 hashing
    are performed. Returns the validated manifest on success.
    """
    package_dir = Path(package_dir)
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise MigrationError(f"package directory not found: {package_dir}")

    manifest_path = package_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise MigrationError(f"missing manifest: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"corrupt manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise MigrationError(f"manifest fields must be exactly {sorted(_MANIFEST_KEYS)}")

    if type(manifest["package_version"]) is not int or manifest["package_version"] != PACKAGE_VERSION:
        raise MigrationError(f"unsupported package_version: {manifest['package_version']!r}")
    if manifest["core_version"] != phanes.__version__:
        raise MigrationError(f"unsupported core_version: {manifest['core_version']!r}")
    if manifest["python_requires"] != PYTHON_REQUIRES:
        raise MigrationError(f"unsupported python_requires: {manifest['python_requires']!r}")
    if type(manifest["adapter_api_version"]) is not int or manifest["adapter_api_version"] != ADAPTER_API_VERSION:
        raise MigrationError(f"unsupported adapter_api_version: {manifest['adapter_api_version']!r}")
    if not isinstance(manifest["agent_id"], str):
        raise MigrationError("manifest agent_id must be a string")
    try:
        parsed_uuid = uuid.UUID(manifest["agent_id"])
    except ValueError as exc:
        raise MigrationError(f"manifest agent_id is not a valid UUID: {manifest['agent_id']!r}") from exc
    if parsed_uuid.version != 4 or str(parsed_uuid) != manifest["agent_id"]:
        raise MigrationError("manifest agent_id must be a canonical uuid4 string")

    files = manifest["files"]
    if not isinstance(files, dict):
        raise MigrationError("manifest files must be an object")
    expected_files = _expected_package_files()
    if set(files) != expected_files:
        missing = sorted(expected_files - set(files))
        extra = sorted(set(files) - expected_files)
        raise MigrationError(f"manifest file list mismatch: missing={missing}, extra={extra}")
    for rel_path, digest in files.items():
        _validate_relative_path(rel_path)
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            raise MigrationError(f"invalid SHA-256 for {rel_path!r}: {digest!r}")

    on_disk_files, on_disk_dirs = _scan_package(package_dir)
    if on_disk_dirs != _EXPECTED_DIRS:
        raise MigrationError(
            f"unexpected package directories: {sorted(on_disk_dirs ^ _EXPECTED_DIRS)}"
        )
    if on_disk_files != expected_files | {MANIFEST_FILENAME}:
        raise MigrationError(
            f"unexpected package files: {sorted(on_disk_files ^ (expected_files | {MANIFEST_FILENAME}))}"
        )

    for rel_path, digest in files.items():
        actual = _sha256(package_dir / Path(rel_path))
        if actual != digest:
            raise MigrationError(f"SHA-256 mismatch for {rel_path!r}")

    try:
        identity = load_identity(package_dir / "self")
        MemoryStore.load(package_dir / "self", identity.agent_id)
    except (IdentityError, MemoryStoreError) as exc:
        raise MigrationError(f"invalid Self data in package: {exc}") from exc
    if manifest["agent_id"] != identity.agent_id:
        raise MigrationError("manifest agent_id does not match Self identity agent_id")

    return manifest


def export_package(state_dir: Path | str, out_dir: Path | str) -> Path:
    """Export Self + whitelisted runtime sources into a validated package.

    Built in a sibling temporary directory and committed by rename; the
    target must not exist and a failure leaves nothing behind.
    """
    state_dir = Path(state_dir)
    out_dir = Path(out_dir)
    # Package v1 cannot represent P1 Self. Never silently drop Experience.
    # Keep this P0 tool independent of P1 modules for isolated v1 runtimes.
    experience_path = state_dir / "experiences.json"
    if experience_path.exists() or experience_path.is_symlink():
        raise MigrationError("package v1 cannot export a P1 Experience Self")
    identity = load_identity(state_dir)
    MemoryStore.load(state_dir, identity.agent_id)
    if out_dir.exists():
        raise MigrationError(f"package target already exists: {out_dir}")

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=out_dir.parent, prefix=out_dir.name + ".tmp-"))
    try:
        (tmp_dir / "self").mkdir()
        runtime_out = tmp_dir / "runtime" / "phanes"
        runtime_out.mkdir(parents=True)
        shutil.copy2(state_dir / IDENTITY_FILENAME, tmp_dir / "self" / IDENTITY_FILENAME)
        shutil.copy2(state_dir / MEMORY_FILENAME, tmp_dir / "self" / MEMORY_FILENAME)
        runtime_src = Path(__file__).resolve().parent
        for name in RUNTIME_FILES:
            shutil.copy2(runtime_src / name, runtime_out / name)

        files = {rel: _sha256(tmp_dir / Path(rel)) for rel in sorted(_expected_package_files())}
        manifest = {
            "package_version": PACKAGE_VERSION,
            "core_version": phanes.__version__,
            "python_requires": PYTHON_REQUIRES,
            "adapter_api_version": ADAPTER_API_VERSION,
            "agent_id": identity.agent_id,
            "files": files,
        }
        (tmp_dir / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        validate_package(tmp_dir)
        tmp_dir.rename(out_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return out_dir


def import_package(package_dir: Path | str, state_dir: Path | str) -> Identity:
    """Validate a package completely, then restore Self into a new state dir.

    The target must not exist; nothing is overwritten or merged. Import
    restores Self only — it never touches bodies, registries or host config.
    """
    package_dir = Path(package_dir)
    state_dir = Path(state_dir)
    if state_dir.exists():
        raise MigrationError(f"import target already exists: {state_dir}")

    validate_package(package_dir)

    state_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=state_dir.parent, prefix=state_dir.name + ".tmp-"))
    try:
        shutil.copy2(package_dir / "self" / IDENTITY_FILENAME, tmp_dir / IDENTITY_FILENAME)
        shutil.copy2(package_dir / "self" / MEMORY_FILENAME, tmp_dir / MEMORY_FILENAME)
        identity = load_identity(tmp_dir)
        MemoryStore.load(tmp_dir, identity.agent_id)
        tmp_dir.rename(state_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return load_identity(state_dir)
