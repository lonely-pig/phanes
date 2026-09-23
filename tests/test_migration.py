"""P0.5 migration tests (Freeze sections 10, 14).

Covers AT03 (cross-environment Self recovery), AT09 package-content part
(no authority in the package), AT11 (corruption/version/agent_id), AT12
(package path boundaries), export/import atomicity, and the trust boundary
(package code is never executed during validation).
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import unittest
import unittest.mock
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from phanes import __main__ as cli
from phanes import __version__
from phanes.capabilities import CapabilityRegistry
from phanes.contracts import Position
from phanes.core import PhanesCore
from phanes.identity import IDENTITY_FILENAME, init_identity, load_identity
from phanes.memory import MEMORY_FILENAME, MemoryStore
from phanes.migration import (
    RUNTIME_FILES,
    MigrationError,
    _validate_relative_path,
    export_package,
    import_package,
    validate_package,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MigrationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state_a = self.root / "A" / "state"
        self.package = self.root / "B" / "transfer"
        self.state_b = self.root / "B" / "state"

    def make_state_a(self):
        """Environment A: identity + remembered Alpha. A also *has* a host
        config granting move_to — which must never enter the package."""
        identity = init_identity(self.state_a)
        memory = MemoryStore.create(self.state_a, identity.agent_id)
        memory.remember_place("Alpha", 10, 20)
        host_config = self.root / "A" / "host.json"
        host_config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "body": {"adapter": "mock_uav", "body_id": "mock-uav-A"},
                    "allowed_capabilities": ["get_position", "move_to"],
                }
            ),
            encoding="utf-8",
        )
        return identity

    def export(self):
        self.make_state_a()
        return export_package(self.state_a, self.package)

    def rewrite_manifest(self, package: Path, mutate):
        manifest_path = package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(manifest)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return manifest

    def rehash(self, package: Path, rel: str):
        def mutate(manifest):
            manifest["files"][rel] = sha256(package / Path(rel))

        self.rewrite_manifest(package, mutate)


class TestExportFormat(MigrationTestCase):
    def test_package_structure_and_manifest(self):
        identity = self.make_state_a()
        export_package(self.state_a, self.package)

        expected_files = {"manifest.json", "self/identity.json", "self/memory.json"}
        expected_files.update(f"runtime/phanes/{name}" for name in RUNTIME_FILES)
        on_disk = {
            str(p.relative_to(self.package)).replace("\\", "/")
            for p in self.package.rglob("*")
            if p.is_file()
        }
        self.assertEqual(on_disk, expected_files)

        manifest = json.loads((self.package / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["package_version"], 1)
        self.assertEqual(manifest["core_version"], __version__)
        self.assertEqual(manifest["python_requires"], ">=3.11")
        self.assertEqual(manifest["adapter_api_version"], 1)
        self.assertEqual(manifest["agent_id"], identity.agent_id)
        for rel, digest in manifest["files"].items():
            self.assertEqual(digest, sha256(self.package / Path(rel)), rel)

    def test_package_contains_no_authority_and_no_forbidden_content(self):
        """AT09 (package content): A had move_to granted; the package must
        contain no grants, no host config, no adapter, no tests/examples."""
        self.export()
        all_text = ""
        for path in self.package.rglob("*"):
            rel = str(path.relative_to(self.package)).replace("\\", "/")
            self.assertNotIn("phanes_host", rel)
            self.assertNotIn("host", rel)
            self.assertFalse(rel.startswith(("tests/", "examples/", ".git/")))
            self.assertNotIn("__pycache__", rel)
            if path.is_file():
                all_text += path.read_text(encoding="utf-8", errors="replace")
        # The manifest and Self data must carry no grant information. (The
        # generic runtime sources legitimately mention the allowed list
        # parameter name; that is code, not authority.)
        manifest_text = (self.package / "manifest.json").read_text(encoding="utf-8")
        self_text = (self.package / "self" / "identity.json").read_text(
            encoding="utf-8"
        ) + (self.package / "self" / "memory.json").read_text(encoding="utf-8")
        self.assertNotIn("allowed_capabilities", manifest_text)
        self.assertNotIn("allowed_capabilities", self_text)
        self.assertNotIn("mock-uav-A", all_text)  # A's body_id / host binding
        # The adapter implementation stays on the host; only the generic
        # discovery loader (which references it by name) migrates.
        self.assertNotIn("[MockUAV] moving to", all_text)

    def test_export_refuses_existing_target(self):
        self.make_state_a()
        self.package.parent.mkdir(parents=True)
        self.package.mkdir()
        with self.assertRaises(MigrationError):
            export_package(self.state_a, self.package)
        self.assertEqual(list(self.package.iterdir()), [])  # untouched

    def test_export_from_corrupt_state_fails(self):
        self.make_state_a()
        (self.state_a / MEMORY_FILENAME).write_text("{ corrupt", encoding="utf-8")
        with self.assertRaises(Exception):
            export_package(self.state_a, self.package)
        self.assertFalse(self.package.exists())

    def test_export_failure_leaves_no_partial_package_and_retries(self):
        self.make_state_a()
        original_copy2 = shutil.copy2

        def failing_copy2(src, dst):
            if str(src).endswith("memory.py"):
                raise OSError("simulated export failure")
            return original_copy2(src, dst)

        with unittest.mock.patch("phanes.migration.shutil.copy2", side_effect=failing_copy2):
            with self.assertRaises(OSError):
                export_package(self.state_a, self.package)
        self.assertFalse(self.package.exists())
        leftovers = [p for p in self.package.parent.iterdir() if ".tmp-" in p.name]
        self.assertEqual(leftovers, [])

        export_package(self.state_a, self.package)
        validate_package(self.package)


class TestImportHappyPath(MigrationTestCase):
    def test_self_continuity_across_environments(self):
        """AT03: A exports, B imports; Identity fields and Memory identical;
        B never creates a new identity."""
        identity_a = self.make_state_a()
        export_package(self.state_a, self.package)

        identity_b = import_package(self.package, self.state_b)

        self.assertEqual(identity_b, identity_a)
        self.assertEqual(identity_b.agent_id, identity_a.agent_id)
        self.assertEqual(identity_b.created_at, identity_a.created_at)
        memory_b = MemoryStore.load(self.state_b, identity_b.agent_id)
        self.assertEqual(memory_b.lookup_place("Alpha"), Position(x=10, y=20))

    def test_import_restores_self_only_and_grants_no_authority(self):
        """AT09 (behavior): after import, without B's own host config there
        is no body and zero authority; 去 Alpha is NO_BODY, not a move."""
        self.export()
        identity_b = import_package(self.package, self.state_b)

        memory_b = MemoryStore.load(self.state_b, identity_b.agent_id)
        registry = CapabilityRegistry()  # B has not configured anything
        core = PhanesCore(identity_b, memory_b, registry)
        result = core.handle_command("去 Alpha。")
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "NO_BODY")
        self.assertIsNone(core.current_body)
        self.assertEqual(registry.list(), ())

    def test_import_refuses_existing_target(self):
        self.export()
        self.state_b.mkdir(parents=True)
        marker = self.state_b / "keep.me"
        marker.write_text("original", encoding="utf-8")
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)
        self.assertEqual(marker.read_text(encoding="utf-8"), "original")

    def test_import_failure_leaves_no_startable_state(self):
        self.export()
        with unittest.mock.patch.object(Path, "rename", side_effect=OSError("simulated commit failure")):
            with self.assertRaises(OSError):
                import_package(self.package, self.state_b)
        self.assertFalse(self.state_b.exists())
        leftovers = [p for p in self.state_b.parent.iterdir() if ".tmp-" in p.name]
        self.assertEqual(leftovers, [])


class TestImportValidation(MigrationTestCase):
    def test_tampered_file_rejected_and_target_unchanged(self):
        self.export()
        core_py = self.package / "runtime" / "phanes" / "core.py"
        core_py.write_text(core_py.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)
        self.assertFalse(self.state_b.exists())

    def test_tampered_self_rejected(self):
        self.export()
        mem = self.package / "self" / "memory.json"
        data = json.loads(mem.read_text(encoding="utf-8"))
        data["places"]["Alpha"] = {"x": 999, "y": 999}
        mem.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)
        self.assertFalse(self.state_b.exists())

    def test_missing_file_rejected(self):
        self.export()
        (self.package / "runtime" / "phanes" / "discovery.py").unlink()
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)
        self.assertFalse(self.state_b.exists())

    def test_extra_file_rejected(self):
        self.export()
        (self.package / "runtime" / "phanes" / "evil.py").write_text("# extra", encoding="utf-8")
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)
        self.assertFalse(self.state_b.exists())

    def test_extra_directory_rejected(self):
        self.export()
        (self.package / "runtime" / "phanes_host").mkdir()
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)

    def test_manifest_file_list_mismatch_rejected(self):
        self.export()

        def drop_entry(manifest):
            del manifest["files"]["runtime/phanes/core.py"]

        self.rewrite_manifest(self.package, drop_entry)
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)

    def test_path_traversal_in_manifest_rejected_before_copy(self):
        self.export()

        def traversal(manifest):
            manifest["files"]["../escape.txt"] = manifest["files"].pop("self/identity.json")

        self.rewrite_manifest(self.package, traversal)
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)
        self.assertFalse((self.root / "B" / "escape.txt").exists())
        self.assertFalse(self.state_b.exists())

    def test_relative_path_validator(self):
        for bad in ("../x", "a/../../b", "/abs/path", "C:/win", "a\\b", "a//b", ".", "", None, 42):
            with self.assertRaises(MigrationError, msg=repr(bad)):
                _validate_relative_path(bad)
        for good in ("self/identity.json", "runtime/phanes/core.py"):
            _validate_relative_path(good)

    def test_symlink_rejected(self):
        self.export()
        link = self.package / "self" / "linked.json"
        try:
            os.symlink(self.package / "self" / "memory.json", link)
            patch_ctx = contextlib.nullcontext()
        except OSError:
            # Windows without symlink privilege: simulate a symlinked entry.
            real_scandir = os.scandir

            class FakeEntry:
                def __init__(self, entry):
                    self._entry = entry

                def __getattr__(self, name):
                    return getattr(self._entry, name)

                def is_symlink(self):
                    return True

            class FakeScandir:
                def __init__(self, path):
                    self._real = real_scandir(path)

                def __enter__(self):
                    with self._real as entries:
                        return [FakeEntry(e) for e in entries]

                def __exit__(self, *exc):
                    return False

            patch_ctx = unittest.mock.patch(
                "os.scandir", side_effect=lambda path: FakeScandir(path)
            )

        with patch_ctx:
            with self.assertRaises(MigrationError):
                import_package(self.package, self.state_b)
        self.assertFalse(self.state_b.exists())

    def test_agent_id_mismatch_rejected_in_consistent_package(self):
        """A maliciously consistent package (hashes recomputed) is still
        rejected by the Self cross-checks."""
        self.export()
        mem = self.package / "self" / "memory.json"
        data = json.loads(mem.read_text(encoding="utf-8"))
        data["agent_id"] = str(uuid.uuid4())
        mem.write_text(json.dumps(data), encoding="utf-8")
        self.rehash(self.package, "self/memory.json")
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)

        self.rewrite_manifest(
            self.package, lambda m: m.update(agent_id=str(uuid.uuid4()))
        )
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)
        self.assertFalse(self.state_b.exists())

    def test_version_mismatches_rejected(self):
        self.make_state_a()
        for field, bad in (
            ("package_version", 2),
            ("package_version", "1"),
            ("core_version", "9.9"),
            ("python_requires", ">=3.9"),
            ("adapter_api_version", 2),
        ):
            export_package(self.state_a, self.package)
            self.rewrite_manifest(self.package, lambda m, f=field, b=bad: m.update({f: b}))
            with self.assertRaises(MigrationError, msg=f"{field}={bad!r}"):
                import_package(self.package, self.state_b)
            self.assertFalse(self.state_b.exists())
            shutil.rmtree(self.package)

    def test_invalid_hash_format_rejected(self):
        self.export()
        self.rewrite_manifest(
            self.package,
            lambda m: m["files"].update({"self/identity.json": "not-a-hash"}),
        )
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)

    def test_missing_manifest_rejected(self):
        self.export()
        (self.package / "manifest.json").unlink()
        with self.assertRaises(MigrationError):
            import_package(self.package, self.state_b)

    def test_package_root_symlink_or_missing_rejected(self):
        self.export()
        with self.assertRaises(MigrationError):
            import_package(self.root / "B" / "no-such-package", self.state_b)


class TestTrustBoundary(MigrationTestCase):
    def test_validation_never_imports_package_code(self):
        """The package runtime is data during validation: no module may be
        imported from the package directory."""
        self.export()
        imported_from_package = []

        real_import = __import__

        def tracking_import(name, *args, **kwargs):
            module = real_import(name, *args, **kwargs)
            module_file = getattr(module, "__file__", None)
            if module_file and str(self.package) in str(module_file):
                imported_from_package.append(name)
            return module

        with unittest.mock.patch("builtins.__import__", side_effect=tracking_import):
            validate_package(self.package)
            import_package(self.package, self.state_b)
        self.assertEqual(imported_from_package, [])


class TestCliMigration(MigrationTestCase):
    def test_export_import_via_cli(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["init", "--state", str(self.state_a)])
        identity_a = load_identity(self.state_a)
        memory = MemoryStore.load(self.state_a, identity_a.agent_id)
        memory.remember_place("Alpha", 10, 20)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["export", "--state", str(self.state_a), "--out", str(self.package)])
        self.assertEqual(rc, 0)
        self.assertIn("exported package:", out.getvalue())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["import", "--package", str(self.package), "--state", str(self.state_b)])
        self.assertEqual(rc, 0)
        self.assertIn(f"agent_id: {identity_a.agent_id}", out.getvalue())
        self.assertEqual(load_identity(self.state_b), identity_a)

    def test_export_cli_failure_returns_nonzero(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cli.main(
                ["export", "--state", str(self.root / "no-state"), "--out", str(self.package)]
            )
        self.assertEqual(rc, 1)
        self.assertIn("error: export failed:", err.getvalue())
        self.assertFalse(self.package.exists())


if __name__ == "__main__":
    unittest.main()
