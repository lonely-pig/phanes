"""P2.1 real package-v2 Runtime plus independently installed Host code."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from phanes.experience_store import ExperienceStore
from phanes.identity import init_identity
from phanes.memory import MemoryStore
from phanes.migration_v2 import SELF_FILES_V2, export_package_v2, import_package_v2, validate_package_v2

REPO_ROOT = Path(__file__).resolve().parent.parent

# Static imports only. These are explicit trusted deployment roots, not
# plugin/module paths taken from a descriptor, config, or migration metadata.
CHILD_PROBE = r'''
import hashlib, importlib.util, json, os, sys
from pathlib import Path
runtime_root, host_root, repo_root, old_a = map(Path, sys.argv[1:5])
missing = sys.argv[5] == "missing"
sys.path[:0] = [str(runtime_root), str(host_root)]
for entry in sys.path:
    resolved = Path(entry).resolve()
    assert not resolved.is_relative_to(repo_root), ("repository on sys.path", entry)
    assert not resolved.is_relative_to(old_a), ("A on sys.path", entry)
assert not old_a.exists()
import phanes, phanes.contracts, phanes_host
from phanes_host import p2_bootstrap
assert callable(p2_bootstrap.onboard_host_session)
assert p2_bootstrap._published is False
assert "phanes.discovery" not in sys.modules
assert "phanes_host._p0_discovery" not in sys.modules
assert "phanes_host.mock_uav" not in sys.modules
assert importlib.util.find_spec("phanes.discovery") is None
session_declarations = [
    name for name, value in vars(p2_bootstrap).items()
    if isinstance(value, p2_bootstrap.HostSessionDescriptor)
]
assert session_declarations == []
evidence = {
    "pid": os.getpid(),
    "sys_path": sys.path,
    "runtime_root": str(runtime_root),
    "host_root": str(host_root),
    "origins": {
        "phanes": phanes.__file__,
        "contracts": phanes.contracts.__file__,
        "phanes_host": phanes_host.__file__,
        "p2_bootstrap": p2_bootstrap.__file__,
    },
    "session_created": bool(session_declarations),
    "session_declarations": session_declarations,
}
if missing:
    try:
        p2_bootstrap._load_host_discovery()
    except ImportError as exc:
        assert "_p0_discovery" in str(exc), repr(exc)
        evidence["missing_dependency_error"] = type(exc).__name__ + ": " + str(exc)
    else:
        raise AssertionError("missing fixed Host dependency did not fail")
    assert "phanes.discovery" not in sys.modules
    assert "phanes_host._p0_discovery" not in sys.modules
    # P2.2 explicit onboarding must fail closed too, before any compose/UUID
    # or current-context read; this is not an isolated success/E2E claim.
    from unittest.mock import Mock, patch
    providers = dict(get_environment_id=Mock(), get_asserted_frame_id=Mock(), get_evaluation_time=Mock())
    config = {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": "body-b"}, "allowed_capabilities": []}
    with patch("uuid.uuid4") as generated, patch("phanes.p1_runtime.compose_p1_runtime") as composed:
        try:
            p2_bootstrap.onboard_host_session("unused-self", config, **providers)
        except p2_bootstrap.OnboardingError as exc:
            assert isinstance(exc.__cause__, ImportError)
            evidence["onboarding_dependency_error"] = str(exc)
        else:
            raise AssertionError("missing Host dependency allowed publication")
        generated.assert_not_called()
        composed.assert_not_called()
    assert not p2_bootstrap._published
    for provider in providers.values():
        provider.assert_not_called()
    assert "phanes.discovery" not in sys.modules
    assert "phanes_host.mock_uav" not in sys.modules
else:
    discovery = p2_bootstrap._load_host_discovery()
    assert discovery.__name__ == "phanes_host._p0_discovery"
    assert discovery._ADAPTER_FACTORIES.keys() == {"mock_uav"}
    assert "phanes_host.mock_uav" not in sys.modules
    assert discovery.BodyAdapter is phanes.contracts.BodyAdapter
    assert p2_bootstrap.BodyDescriptor is phanes.contracts.BodyDescriptor
    assert p2_bootstrap.CapabilitySpec is phanes.contracts.CapabilitySpec
    evidence["origins"]["_p0_discovery"] = discovery.__file__
    evidence["discovery_sha256"] = hashlib.sha256(Path(discovery.__file__).read_bytes()).hexdigest()
for name, origin in evidence["origins"].items():
    expected = runtime_root if name in {"phanes", "contracts"} else host_root
    assert Path(origin).resolve().is_relative_to(expected), (name, origin)
assert not (runtime_root / "phanes" / "discovery.py").exists()
print(json.dumps(evidence, sort_keys=True))
'''


class TestP2IsolatedDeployment(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.a = self.root / "A"
        source_state = self.a / "state"
        identity = init_identity(source_state)
        MemoryStore.create(source_state, identity.agent_id)
        ExperienceStore.create(source_state, identity.agent_id)
        self.before_self = {name: (source_state / name).read_bytes() for name in SELF_FILES_V2}
        exported = export_package_v2(source_state, self.a / "package-v2")

        self.b = self.root / "B"
        self.b.mkdir()
        self.package = self.b / "copied-package-v2"
        shutil.copytree(exported, self.package)
        self.runtime = self.package / "runtime"
        self.state = self.b / "state"
        import_package_v2(self.package, self.state)
        self.host = self.b / "trusted_host"
        host_package = self.host / "phanes_host"
        host_package.mkdir(parents=True)
        for filename in ("__init__.py", "mock_uav.py", "p2_bootstrap.py"):
            shutil.copyfile(REPO_ROOT / "phanes_host" / filename, host_package / filename)
        self.discovery_source = (REPO_ROOT / "phanes" / "discovery.py").read_bytes()
        self.installed_discovery = host_package / "_p0_discovery.py"
        # Deployment-only artifact; no fifth maintained repository file.
        shutil.copyfile(REPO_ROOT / "phanes" / "discovery.py", self.installed_discovery)
        self.before_package = {
            path.relative_to(self.package).as_posix(): path.read_bytes()
            for path in self.package.rglob("*") if path.is_file()
        }
        self.a.rename(self.root / "A_unavailable")

    def probe(self, missing=False):
        env = dict(os.environ)
        # Deliberately hostile inherited path: -I must ignore it.
        env["PYTHONPATH"] = os.pathsep.join((str(REPO_ROOT), str(self.a)))
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", CHILD_PROBE,
             str(self.runtime), str(self.host), str(REPO_ROOT), str(self.a),
             "missing" if missing else "present"],
            cwd=self.b, env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout)
        self.assertNotEqual(evidence["pid"], os.getpid())
        self.assertEqual(evidence["sys_path"][:2], [str(self.runtime), str(self.host)])
        self.assertFalse(evidence["session_created"])
        for entry in evidence["sys_path"]:
            self.assertFalse(Path(entry).resolve().is_relative_to(REPO_ROOT))
            self.assertFalse(Path(entry).resolve().is_relative_to(self.a))
        for name, origin in evidence["origins"].items():
            expected = self.runtime if name in {"phanes", "contracts"} else self.host
            self.assertTrue(Path(origin).resolve().is_relative_to(expected), (name, origin))
        # Test-local module imports must not mutate Self or the package.
        self.assertEqual(self.before_self, {name: (self.state / name).read_bytes() for name in SELF_FILES_V2})
        self.assertEqual(self.before_package, {
            path.relative_to(self.package).as_posix(): path.read_bytes()
            for path in self.package.rglob("*") if path.is_file()
        })
        validate_package_v2(self.package)
        print("P2.1_DEPLOYMENT " + json.dumps(evidence, sort_keys=True))
        return evidence

    def test_host_discovery_copy_is_byte_identical_to_p0_source(self):
        self.assertEqual(self.installed_discovery.read_bytes(), self.discovery_source)
        self.assertEqual(
            hashlib.sha256(self.installed_discovery.read_bytes()).hexdigest(),
            hashlib.sha256(self.discovery_source).hexdigest(),
        )
        self.assertFalse((REPO_ROOT / "phanes_host" / "_p0_discovery.py").exists())
        manifest = validate_package_v2(self.package)
        self.assertNotIn("runtime/phanes/discovery.py", manifest["files"])
        self.assertFalse(any("phanes_host" in name or "p2_bootstrap" in name for name in manifest["files"]))

    def test_installed_host_imports_with_only_v2_runtime_and_host_paths(self):
        evidence = self.probe()
        self.assertEqual(evidence["discovery_sha256"], hashlib.sha256(self.discovery_source).hexdigest())
        self.assertEqual(Path(evidence["origins"]["_p0_discovery"]), self.installed_discovery)

    def test_missing_host_discovery_has_no_repository_fallback(self):
        self.installed_discovery.unlink()
        evidence = self.probe(missing=True)
        self.assertIn("_p0_discovery", evidence["missing_dependency_error"])
        self.assertNotIn("_p0_discovery", evidence["origins"])


if __name__ == "__main__":
    unittest.main()
