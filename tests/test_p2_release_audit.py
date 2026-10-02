"""P2.6 release-candidate audits: package-v2 whitelist and migration exclusions.

Programmatic re-verification that the P1 package-v2 transport is unchanged by
P2 and that no P2 session/host state reaches Self or package bytes after a
real onboarding. The whitelists below are pinned literally, not imported, so
any accidental extension of the migration surface fails here.
"""

import importlib.util
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phanes.experience_store import ExperienceStore
from phanes.identity import init_identity
from phanes.memory import MemoryStore
from phanes.migration_v2 import (
    PACKAGE_VERSION_V2,
    P1_CORE_VERSION,
    RUNTIME_FILES_V2,
    SELF_FILES_V2,
    export_package_v2,
)
from test_experience_semantics import record

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_SELF_FILES = ("identity.json", "memory.json", "experiences.json")
EXPECTED_RUNTIME_FILES = (
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

# P2 additions must never appear in migrated bytes. Provider parameter names
# are P1's own composition surface and are deliberately not listed here.
P2_SESSION_MARKERS = (
    b"host_session_id",
    b"HostSessionDescriptor",
    b"OnboardingError",
    b"onboard_host_session",
    b"p2_bootstrap",
)


class TestP2MigrationExclusionAudit(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        identity = init_identity(self.state)
        MemoryStore.create(self.state, identity.agent_id)
        store = ExperienceStore.create(self.state, identity.agent_id, test_mode=True)
        item = record(agent_id=identity.agent_id)
        item["dependencies"]["body"] = {"mode": "EXACT", "id": "body-a"}
        store.append_record(item)
        self.experience_id = item["experience_id"]

    def test_package_v2_whitelist_is_pinned_without_p2_additions(self):
        self.assertEqual(SELF_FILES_V2, EXPECTED_SELF_FILES)
        self.assertEqual(RUNTIME_FILES_V2, EXPECTED_RUNTIME_FILES)
        self.assertFalse(any("host" in name or "discovery" in name for name in RUNTIME_FILES_V2))

    def test_onboarded_self_and_package_carry_no_p2_session_markers(self):
        # Standard isolated-bootstrap pattern (same source file as production).
        discovery_source = self.root / "_p0_discovery.py"
        shutil.copyfile(REPO_ROOT / "phanes" / "discovery.py", discovery_source)
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)

        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module

        discovery = load("phanes_host._p0_discovery", discovery_source)
        import phanes_host

        host_dependency = patch.object(phanes_host, "_p0_discovery", discovery, create=True)
        host_dependency.start()
        self.addCleanup(host_dependency.stop)
        bootstrap = load(
            f"_p2_audit_bootstrap_{uuid.uuid4().hex[:8]}",
            REPO_ROOT / "phanes_host" / "p2_bootstrap.py",
        )

        config = {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": "body-a"},
                  "allowed_capabilities": []}
        descriptor, runtime = bootstrap.onboard_host_session(
            self.state, config, allowed_capabilities=("move_to",),
            get_environment_id=lambda: "env-a", get_asserted_frame_id=lambda: "frame-a",
            get_evaluation_time=lambda: None)
        self.assertTrue(runtime.navigate_experience(self.experience_id).ok)

        package = export_package_v2(self.state, self.root / "audited-package")
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["package_version"], PACKAGE_VERSION_V2)
        self.assertEqual(manifest["core_version"], P1_CORE_VERSION)
        self.assertNotEqual(manifest["agent_id"], descriptor.host_session_id)
        migrated = {
            path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*") if path.is_file()
        }
        self.assertEqual(
            set(manifest["files"]), {f"self/{name}" for name in EXPECTED_SELF_FILES}
            | {f"runtime/phanes/{name}" for name in EXPECTED_RUNTIME_FILES}
        )
        self.assertEqual(
            set(migrated), set(manifest["files"]) | {"manifest.json"},
            "package tree contains files outside the pinned whitelist",
        )
        for name, data in migrated.items():
            for marker in P2_SESSION_MARKERS:
                self.assertNotIn(marker, data, (name, marker))
            self.assertNotIn(descriptor.host_session_id.encode("utf-8"), data, name)


if __name__ == "__main__":
    unittest.main()
