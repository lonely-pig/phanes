"""P1.6 package-v2: complete Self, closed Runtime, atomic inert import."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phanes.capabilities import CapabilityRegistry
from phanes.contracts import CAPABILITY_UNAVAILABLE
from phanes.experience_store import ExperienceStore
from phanes.identity import init_identity, load_identity
from phanes.memory import MemoryStore
from phanes.migration import MigrationError, export_package, import_package
from phanes.migration_v2 import (
    MANIFEST_FILENAME, RUNTIME_FILES_V2, SELF_FILES_V2, MigrationV2Error,
    export_package_v2, import_package_v2, validate_package_v2,
)
import phanes.migration_v2 as migration_v2
from phanes.p1_runtime import compose_p1_runtime
from test_experience_semantics import record


class MigrationV2Case(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "A"
        self.package = self.root / "package"
        self.target = self.root / "B"
        self.identity = init_identity(self.source)
        self.memory = MemoryStore.create(self.source, self.identity.agent_id)
        self.memory.remember_place("legacy", 7, 8)
        self.store = ExperienceStore.create(self.source, self.identity.agent_id)
        self.first = record(agent_id=self.identity.agent_id)
        self.store.append_record(self.first)
        self.correction = record(agent_id=self.identity.agent_id)
        self.correction["payload"]["position"] = {"x": 10**400, "y": -(10**400)}
        self.correction["derived_from"] = [self.first["experience_id"]]
        self.correction["supersedes"] = self.first["experience_id"]
        self.store.append_record(self.correction)

    def export(self):
        return export_package_v2(self.source, self.package)

    def manifest(self):
        return json.loads((self.package / MANIFEST_FILENAME).read_text(encoding="utf-8"))

    def write_manifest(self, value):
        (self.package / MANIFEST_FILENAME).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def rehash(self, rel):
        value = self.manifest()
        value["files"][rel] = hashlib.sha256((self.package / rel).read_bytes()).hexdigest()
        self.write_manifest(value)

    def assert_no_temp(self, target):
        self.assertEqual(list(target.parent.glob(target.name + ".tmp-*")), [])


class TestV2RoundTrip(MigrationV2Case):
    def test_exact_whitelist_versions_and_complete_self_round_trip(self):
        before = {name: (self.source / name).read_bytes() for name in SELF_FILES_V2}
        self.export()
        manifest = validate_package_v2(self.package)
        expected = {f"self/{name}" for name in SELF_FILES_V2} | {
            f"runtime/phanes/{name}" for name in RUNTIME_FILES_V2
        }
        self.assertEqual(set(manifest["files"]), expected)
        self.assertEqual((manifest["package_version"], manifest["core_version"]), (2, "0.2"))
        self.assertEqual(set(SELF_FILES_V2), {"identity.json", "memory.json", "experiences.json"})
        self.assertTrue({"p1_runtime.py", "experience_core.py", "experience_gateway.py"} <= set(RUNTIME_FILES_V2))
        self.assertFalse({"__main__.py", "core.py", "discovery.py", "migration.py", "migration_v2.py"} & set(RUNTIME_FILES_V2))
        self.assertFalse(any("phanes_host" in rel or "host-config" in rel for rel in manifest["files"]))
        imported = import_package_v2(self.package, self.target)
        self.assertEqual(imported.agent_id, self.identity.agent_id)
        self.assertEqual(before, {name: (self.target / name).read_bytes() for name in SELF_FILES_V2})
        self.assertEqual(ExperienceStore.load(self.target, imported.agent_id).document_snapshot(),
                         self.store.document_snapshot())
        self.assertEqual(MemoryStore.load(self.target, imported.agent_id).lookup_place("legacy").x, 7)
        self.assert_no_temp(self.package)
        self.assert_no_temp(self.target)

    def test_carried_generic_runtime_imports_without_p0_legacy_bootstrap(self):
        self.export()
        code = "import sys; sys.path.insert(0, sys.argv[1]); import phanes.p1_runtime; import phanes.experience_core; print('OK')"
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(self.package / "runtime")],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "OK"), result.stderr)

    def test_import_is_inert_and_restores_no_authority(self):
        self.export()
        with (
            patch("phanes.experience_gateway.evaluate_experience_use", side_effect=AssertionError("evaluated")) as evaluated,
            patch.object(CapabilityRegistry, "bind", side_effect=AssertionError("bound")) as bound,
            patch.object(CapabilityRegistry, "list", side_effect=AssertionError("listed")) as listed,
            patch.object(CapabilityRegistry, "invoke", side_effect=AssertionError("action")) as invoked,
            patch("phanes.discovery.discover_body", side_effect=AssertionError("discovered")) as discovered,
            patch("phanes.experience_gateway.ExperienceUseGateway.request", side_effect=AssertionError("gateway")) as requested,
            patch("phanes.experience_gateway.TargetContextSources.snapshot", side_effect=AssertionError("context")) as context,
            patch("phanes_host.mock_uav.MockUAVAdapter.invoke", side_effect=AssertionError("adapter")) as adapted,
        ):
            imported = import_package_v2(self.package, self.target)
        evaluated.assert_not_called()
        bound.assert_not_called()
        listed.assert_not_called()
        invoked.assert_not_called()
        discovered.assert_not_called()
        requested.assert_not_called()
        context.assert_not_called()
        adapted.assert_not_called()
        self.assertEqual(imported.agent_id, self.identity.agent_id)
        registry = CapabilityRegistry()
        # A fresh Host may bind a Body but carries no grant from the package.
        from phanes_host.mock_uav import MockUAVAdapter
        registry.bind(MockUAVAdapter("body-b"), [])
        runtime = compose_p1_runtime(
            self.target, registry,
            get_environment_id=lambda: "env-a",
            get_asserted_frame_id=lambda: "frame-a",
            get_evaluation_time=lambda: None,
        )
        result = runtime.navigate_experience(self.correction["experience_id"])
        self.assertEqual(result.error.code, CAPABILITY_UNAVAILABLE)

    def test_export_is_inert_and_v1_import_rejects_v2(self):
        with patch("phanes.experience_gateway.evaluate_experience_use", side_effect=AssertionError("evaluated")) as evaluated:
            with patch.object(CapabilityRegistry, "bind", side_effect=AssertionError("bound")) as bound:
                with patch.object(CapabilityRegistry, "invoke", side_effect=AssertionError("action")) as invoked:
                    self.export()
        evaluated.assert_not_called()
        bound.assert_not_called()
        invoked.assert_not_called()
        with self.assertRaises(MigrationError):
            import_package(self.package, self.target)
        self.assertFalse(self.target.exists())

    def test_package_source_is_never_executed_even_with_self_consistent_hash(self):
        self.export()
        path = self.package / "runtime" / "phanes" / "p1_runtime.py"
        path.write_text("raise RuntimeError('package code executed')\n", encoding="utf-8")
        self.rehash("runtime/phanes/p1_runtime.py")
        validate_package_v2(self.package)
        import_package_v2(self.package, self.target)
        self.assertEqual(load_identity(self.target).agent_id, self.identity.agent_id)

    def test_export_and_import_refuse_existing_targets(self):
        self.package.mkdir()
        with self.assertRaises(MigrationV2Error):
            export_package_v2(self.source, self.package)
        self.assertEqual(list(self.package.iterdir()), [])
        self.package.rmdir()
        self.export()
        self.target.mkdir()
        marker = self.target / "marker"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaises(MigrationV2Error):
            import_package_v2(self.package, self.target)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_transport_targets_cannot_overlap_the_input_tree(self):
        with self.assertRaises(MigrationV2Error):
            export_package_v2(self.source, self.source / "nested-package")
        self.assertEqual(set(path.name for path in self.source.iterdir()), set(SELF_FILES_V2))
        self.export()
        with self.assertRaises(MigrationV2Error):
            import_package_v2(self.package, self.package / "nested-state")
        self.assertFalse((self.package / "nested-state").exists())


class TestVersionAndSelfBoundary(MigrationV2Case):
    def test_v1_export_refuses_p1_self_without_partial_package(self):
        with self.assertRaisesRegex(MigrationError, "cannot export a P1"):
            export_package(self.source, self.package)
        self.assertFalse(self.package.exists())

    def test_v2_export_requires_experience_and_rejects_p0_only(self):
        p0 = self.root / "p0"
        identity = init_identity(p0)
        MemoryStore.create(p0, identity.agent_id)
        with self.assertRaises(MigrationV2Error):
            export_package_v2(p0, self.package)
        self.assertFalse(self.package.exists())
        # v1 remains valid for an authentic P0-only Self.
        export_package(p0, self.package)
        with self.assertRaises(MigrationV2Error):
            import_package_v2(self.package, self.target)
        self.assertFalse(self.target.exists())

    def test_missing_or_corrupt_experience_and_extra_source_files_fail_closed(self):
        experience = self.source / "experiences.json"
        original = experience.read_bytes()
        for data in (None, b"", b"{bad"):
            with self.subTest(data=data):
                if data is None:
                    experience.unlink(missing_ok=True)
                else:
                    experience.write_bytes(data)
                with self.assertRaises(MigrationV2Error):
                    self.export()
                self.assertFalse(self.package.exists())
        experience.write_bytes(original)
        extra = self.source / "host-config.json"
        extra.write_text("{}", encoding="utf-8")
        with self.assertRaises(MigrationV2Error):
            self.export()
        extra.unlink()
        self.export()

    def test_invalid_memory_agent_and_experience_graph_are_rejected(self):
        memory = self.source / "memory.json"
        original_memory = memory.read_bytes()
        invalid = json.loads(memory.read_text(encoding="utf-8"))
        invalid["agent_id"] = self.first["experience_id"]
        memory.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaises(MigrationV2Error):
            self.export()
        memory.write_bytes(original_memory)
        experience = self.source / "experiences.json"
        original_experience = experience.read_bytes()
        invalid = json.loads(experience.read_text(encoding="utf-8"))
        invalid["records"][1]["derived_from"] = [self.correction["experience_id"]]
        experience.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaises(MigrationV2Error):
            self.export()
        experience.write_bytes(original_experience)
        self.export()


class TestPackageValidation(MigrationV2Case):
    def test_version_manifest_agent_and_hash_tampering_rejected(self):
        changes = (
            lambda m: m.update(package_version=1),
            lambda m: m.update(package_version=True),
            lambda m: m.update(core_version="0.1"),
            lambda m: m.update(python_requires=">=99"),
            lambda m: m.update(adapter_api_version=2),
            lambda m: m.update(agent_id=self.first["experience_id"]),
            lambda m: m.update(unexpected=1),
            lambda m: m["files"].pop("self/experiences.json"),
            lambda m: m["files"].update({"../escape": "0" * 64}),
            lambda m: m["files"].update({"self/experiences.json": "x"}),
        )
        for change in changes:
            with self.subTest(change=changes.index(change)):
                self.export()
                manifest = self.manifest()
                change(manifest)
                self.write_manifest(manifest)
                with self.assertRaises(MigrationV2Error):
                    import_package_v2(self.package, self.target)
                self.assertFalse(self.target.exists())
                shutil.rmtree(self.package)
        self.export()
        (self.package / "self" / "experiences.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(MigrationV2Error):
            validate_package_v2(self.package)

    def test_self_consistent_hash_cannot_hide_invalid_experience(self):
        self.export()
        path = self.package / "self" / "experiences.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["records"][1]["supersedes"] = "00000000-0000-4000-8000-000000000000"
        path.write_text(json.dumps(value), encoding="utf-8")
        self.rehash("self/experiences.json")
        with self.assertRaises(MigrationV2Error):
            import_package_v2(self.package, self.target)
        self.assertFalse(self.target.exists())

    def test_extra_file_missing_file_and_symlink_are_rejected(self):
        self.export()
        extra = self.package / "host-config.json"
        extra.write_text("{}", encoding="utf-8")
        with self.assertRaises(MigrationV2Error):
            validate_package_v2(self.package)
        extra.unlink()
        missing = self.package / "self" / "memory.json"
        original = missing.read_bytes()
        missing.unlink()
        with self.assertRaises(MigrationV2Error):
            validate_package_v2(self.package)
        missing.write_bytes(original)
        try:
            os.symlink(self.package / "self" / "memory.json", extra)
        except (OSError, NotImplementedError):
            with patch("phanes.migration_v2._scan_package", side_effect=MigrationError("symlink in package")):
                with self.assertRaises(MigrationV2Error):
                    validate_package_v2(self.package)
            return
        with self.assertRaises(MigrationV2Error):
            validate_package_v2(self.package)


class TestAtomicTransport(MigrationV2Case):
    def test_import_staging_validation_failure_cleans_and_retries(self):
        self.export()
        actual_validator = migration_v2._validate_complete_self
        calls = 0

        def fail_second_validation(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise MigrationV2Error("simulated staged validation failure")
            return actual_validator(*args, **kwargs)

        with patch("phanes.migration_v2._validate_complete_self", side_effect=fail_second_validation):
            with self.assertRaisesRegex(MigrationV2Error, "staged validation failure"):
                import_package_v2(self.package, self.target)
        self.assertEqual(calls, 2)
        self.assertFalse(self.target.exists())
        self.assert_no_temp(self.target)
        import_package_v2(self.package, self.target)

    def test_import_rechecks_staged_self_against_manifest_hash(self):
        self.export()
        before = {name: (self.source / name).read_bytes() for name in SELF_FILES_V2}
        original_copy = shutil.copy2

        def change_after_copy(source, target):
            copied = original_copy(source, target)
            if Path(source).name == "experiences.json":
                path = Path(target)
                value = json.loads(path.read_text(encoding="utf-8"))
                value["records"][1]["payload"]["position"]["x"] = 123
                path.write_text(json.dumps(value), encoding="utf-8")
            return copied

        with patch("phanes.migration_v2.shutil.copy2", side_effect=change_after_copy):
            with self.assertRaisesRegex(MigrationV2Error, "staged Self hash changed"):
                import_package_v2(self.package, self.target)
        self.assertFalse(self.target.exists())
        self.assert_no_temp(self.target)
        self.assertEqual(before, {name: (self.source / name).read_bytes() for name in SELF_FILES_V2})
        import_package_v2(self.package, self.target)

    def test_export_copy_validate_and_commit_failures_cleanup_then_retry(self):
        for patch_target in ("phanes.migration_v2.shutil.copy2", "phanes.migration_v2.validate_package_v2", "pathlib.Path.rename"):
            with self.subTest(failure=patch_target):
                with patch(patch_target, side_effect=OSError("simulated")):
                    with self.assertRaises(OSError):
                        self.export()
                self.assertFalse(self.package.exists())
                self.assert_no_temp(self.package)
                self.export()
                shutil.rmtree(self.package)

    def test_import_copy_validate_and_commit_failures_cleanup_then_retry(self):
        self.export()
        for patch_target in ("phanes.migration_v2.shutil.copy2", "phanes.migration_v2._validate_complete_self", "pathlib.Path.rename"):
            with self.subTest(failure=patch_target):
                with patch(patch_target, side_effect=OSError("simulated")):
                    with self.assertRaises(OSError):
                        import_package_v2(self.package, self.target)
                self.assertFalse(self.target.exists())
                self.assert_no_temp(self.target)
                import_package_v2(self.package, self.target)
                shutil.rmtree(self.target)


if __name__ == "__main__":
    unittest.main()
