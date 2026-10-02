"""P2.3 isolated deployment end-to-end acceptance.

A realistic software deployment boundary (A produces the package, B is an
independent target Host) proves that P2.2 onboarding works with only the
copied package-v2 runtime and the independently installed trusted Host tree.
Every scenario runs in a fresh ``python -I -S -B`` subprocess with a hostile
inherited PYTHONPATH. This milestone adds evidence, not new semantics.
"""

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
from test_experience_semantics import record

REPO_ROOT = Path(__file__).resolve().parent.parent

BODY_ID = "uav-001"
ENVIRONMENT_ID = "env-alpha"
FRAME_ID = "frame-alpha"
POSITION_X, POSITION_Y = 10, 20
POSITION = {"x": POSITION_X, "y": POSITION_Y}

# One child program, one mode per fresh process. Counting wrappers delegate
# to the original trusted implementations, so counted invocations are real.
CHILD_SCRIPT = r'''
import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path

BODY_ID = "uav-001"
ENVIRONMENT_ID = "env-alpha"
FRAME_ID = "frame-alpha"
POSITION = {"x": 10, "y": 20}

runtime_root, host_root, repo_root, old_a = map(Path, sys.argv[1:5])
mode = sys.argv[5]
package_dir, state_dir = map(Path, sys.argv[6:8])
experience_id = sys.argv[8] if len(sys.argv) > 8 else ""

sys.path[:0] = [str(runtime_root), str(host_root)]

# --- P2.3.2 isolation evidence, re-checked in every scenario ---
for entry in sys.path:
    resolved = Path(entry).resolve()
    assert not resolved.is_relative_to(repo_root), ("development repository on sys.path", entry)
    assert not resolved.is_relative_to(old_a), ("deployment A on sys.path", entry)
assert not old_a.exists(), "source deployment A is still available"
try:
    import phanes.discovery
except ImportError:
    assert "phanes.discovery" not in sys.modules
else:
    raise AssertionError("phanes.discovery importable in isolated deployment")

import phanes
import phanes.capabilities as capabilities
import phanes.p1_runtime as p1_runtime
import phanes_host
import phanes_host.mock_uav as mock_uav
from phanes_host import p2_bootstrap
from phanes_host.p2_bootstrap import OnboardingError

origins = {
    "phanes": phanes.__file__,
    "capabilities": capabilities.__file__,
    "p1_runtime": p1_runtime.__file__,
    "phanes_host": phanes_host.__file__,
    "p2_bootstrap": p2_bootstrap.__file__,
    "mock_uav": mock_uav.__file__,
}
for name, origin in origins.items():
    expected = runtime_root if name in {"phanes", "capabilities", "p1_runtime"} else host_root
    assert Path(origin).resolve().is_relative_to(expected), (name, origin)

counters = dict.fromkeys(
    ("discovery", "adapter_init", "adapter_invoke", "registry_init", "registry_invoke",
     "bind", "compose", "uuid"), 0)
provider_calls = dict.fromkeys(("environment", "frame", "time"), 0)

if mode == "import_only":
    # --- P2.3.3 Import != Onboard: patch the dangerous hooks, then import ---
    assert "phanes_host._p0_discovery" not in sys.modules
    original_adapter_init = mock_uav.MockUAVAdapter.__init__
    def fail_adapter_init(self, body_id):
        counters["adapter_init"] += 1
        raise AssertionError("Adapter constructed on import")
    original_adapter_invoke = mock_uav.MockUAVAdapter.invoke
    def fail_adapter_invoke(self, name, args):
        counters["adapter_invoke"] += 1
        raise AssertionError("Adapter invoked on import")
    original_registry_init = capabilities.CapabilityRegistry.__init__
    def fail_registry_init(self):
        counters["registry_init"] += 1
        raise AssertionError("Registry constructed on import")
    original_bind = capabilities.CapabilityRegistry.bind
    def fail_bind(self, adapter, allowed):
        counters["bind"] += 1
        raise AssertionError("bind happened on import")
    original_compose = p1_runtime.compose_p1_runtime
    def fail_compose(*args, **kwargs):
        counters["compose"] += 1
        raise AssertionError("compose happened on import")
    import uuid
    original_uuid4 = uuid.uuid4
    def fail_uuid4():
        counters["uuid"] += 1
        raise AssertionError("session UUID generated on import")
    mock_uav.MockUAVAdapter.__init__ = fail_adapter_init
    mock_uav.MockUAVAdapter.invoke = fail_adapter_invoke
    capabilities.CapabilityRegistry.__init__ = fail_registry_init
    capabilities.CapabilityRegistry.bind = fail_bind
    p1_runtime.compose_p1_runtime = fail_compose
    uuid.uuid4 = fail_uuid4

    import phanes_host.p2_bootstrap  # noqa: F811  the import under test

    assert "phanes_host._p0_discovery" not in sys.modules
    assert p2_bootstrap._published is False
    descriptors = [name for name, value in vars(p2_bootstrap).items()
                   if isinstance(value, p2_bootstrap.HostSessionDescriptor)]
    assert descriptors == []
    mock_uav.MockUAVAdapter.__init__ = original_adapter_init
    mock_uav.MockUAVAdapter.invoke = original_adapter_invoke
    capabilities.CapabilityRegistry.__init__ = original_registry_init
    capabilities.CapabilityRegistry.bind = original_bind
    p1_runtime.compose_p1_runtime = original_compose
    uuid.uuid4 = original_uuid4
    evidence = {
        "pid": os.getpid(),
        "sys_path": sys.path,
        "origins": origins,
        "counters": counters,
        "provider_calls": provider_calls,
        "published": p2_bootstrap._published,
        "module_descriptors": descriptors,
        "discovery_unloaded": "phanes_host._p0_discovery" not in sys.modules,
    }
    print(json.dumps(evidence, sort_keys=True))
    raise SystemExit(0)

# --- remaining modes: explicit onboarding scenarios ---
import phanes_host._p0_discovery as host_discovery

original_discover = host_discovery.discover_body
def counted_discover(config):
    counters["discovery"] += 1
    return original_discover(config)
host_discovery.discover_body = counted_discover

original_adapter_init = mock_uav.MockUAVAdapter.__init__
def counted_adapter_init(self, body_id):
    counters["adapter_init"] += 1
    original_adapter_init(self, body_id)
mock_uav.MockUAVAdapter.__init__ = counted_adapter_init

original_adapter_invoke = mock_uav.MockUAVAdapter.invoke
def counted_adapter_invoke(self, name, args):
    counters["adapter_invoke"] += 1
    return original_adapter_invoke(self, name, args)
mock_uav.MockUAVAdapter.invoke = counted_adapter_invoke

original_registry_init = capabilities.CapabilityRegistry.__init__
def counted_registry_init(self):
    counters["registry_init"] += 1
    original_registry_init(self)
capabilities.CapabilityRegistry.__init__ = counted_registry_init

original_registry_invoke = capabilities.CapabilityRegistry.invoke
def counted_registry_invoke(self, name, args):
    counters["registry_invoke"] += 1
    return original_registry_invoke(self, name, args)
capabilities.CapabilityRegistry.invoke = counted_registry_invoke

bound = []
original_bind = capabilities.CapabilityRegistry.bind
def counted_bind(self, adapter, allowed):
    counters["bind"] += 1
    bound.append(self)
    return original_bind(self, adapter, allowed)
capabilities.CapabilityRegistry.bind = counted_bind

original_compose = p1_runtime.compose_p1_runtime
def counted_compose(*args, **kwargs):
    counters["compose"] += 1
    return original_compose(*args, **kwargs)
p1_runtime.compose_p1_runtime = counted_compose

import uuid
original_uuid4 = uuid.uuid4
def counted_uuid4():
    counters["uuid"] += 1
    return original_uuid4()
uuid.uuid4 = counted_uuid4

def make_provider(key, value):
    def provider():
        provider_calls[key] += 1
        if isinstance(value, BaseException):
            raise value
        return value
    return provider

SELF_FILES = ("identity.json", "memory.json", "experiences.json")

def self_hashes():
    return {name: hashlib.sha256((state_dir / name).read_bytes()).hexdigest() for name in SELF_FILES}

def package_digest():
    digest = hashlib.sha256()
    for path in sorted(path for path in package_dir.rglob("*") if path.is_file()):
        digest.update(path.relative_to(package_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()

SCENARIOS = {
    # allowed, environment, frame, time
    "zero_authority": ((), ENVIRONMENT_ID, FRAME_ID, None),
    "explicit_authority": (("move_to",), ENVIRONMENT_ID, FRAME_ID, None),
    "missing_context": (("move_to",), ENVIRONMENT_ID, None, None),
    "provider_exception": (("move_to",), ENVIRONMENT_ID, RuntimeError("current frame provider failed"), None),
}
allowed, environment, frame, evaluation_time = SCENARIOS[mode]

assert p2_bootstrap._published is False
assert all(call == 0 for call in counters.values())
before = {"self": self_hashes(), "package": package_digest()}

config = {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": BODY_ID}, "allowed_capabilities": []}
providers = dict(
    get_environment_id=make_provider("environment", environment),
    get_asserted_frame_id=make_provider("frame", frame),
    get_evaluation_time=make_provider("time", evaluation_time),
)
onboarded = p2_bootstrap.onboard_host_session(state_dir, config, allowed_capabilities=allowed, **providers)
after_onboarding = {"self": self_hashes(), "package": package_digest()}
assert before == after_onboarding, "onboarding mutated Self or package"
assert p2_bootstrap._published is True
assert onboarded is not None
descriptor, runtime = onboarded
assert counters["adapter_invoke"] == 0 and counters["registry_invoke"] == 0
assert all(call == 0 for call in provider_calls.values()), "providers called during onboarding"

import uuid as uuid_module
session = {
    "body_id": descriptor.body.body_id,
    "body_type": descriptor.body.body_type,
    "host_session_id": descriptor.host_session_id,
    "host_session_id_is_uuid4": uuid_module.UUID(descriptor.host_session_id).version == 4,
    "offered": [spec.name for spec in descriptor.offered_capabilities],
    "authorized": [spec.name for spec in bound[-1].list()],
    "context_source_kinds": list(descriptor.context_source_kinds),
    "explicit_authority": list(allowed),
}

request = None
if experience_id:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            outcome = runtime.navigate_experience(experience_id)
    except BaseException as exc:  # original exception must propagate unchanged
        request = {"kind": "exception", "type": type(exc).__name__, "message": str(exc),
                   "is_onboarding_error": isinstance(exc, OnboardingError)}
    else:
        status = getattr(outcome, "status", None)
        if status is not None:
            request = {"kind": "applicability", "status": status.value,
                       "reasons": list(outcome.reason_codes)}
        elif outcome.ok:
            request = {"kind": "invocation", "ok": True, "data": outcome.data}
        else:
            request = {"kind": "invocation", "ok": False, "code": outcome.error.code}

evidence = {
    "pid": os.getpid(),
    "sys_path": sys.path,
    "origins": {**origins, "_p0_discovery": sys.modules["phanes_host._p0_discovery"].__file__},
    "counters": counters,
    "provider_calls": provider_calls,
    "session": session,
    "request": request,
    "self_hashes_before": before["self"],
    "self_hashes_after": self_hashes(),
    "package_digest_before": before["package"],
    "package_digest_after": package_digest(),
}
print(json.dumps(evidence, sort_keys=True))
'''


class TestP2IsolatedOnboardingAcceptance(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

        # A: original state and trusted source/package producer.
        self.a = self.root / "A"
        source_state = self.a / "state"
        identity = init_identity(source_state)
        MemoryStore.create(source_state, identity.agent_id)
        store = ExperienceStore.create(source_state, identity.agent_id, test_mode=True)
        item = record(agent_id=identity.agent_id)
        item["payload"]["position"] = dict(POSITION)
        item["dependencies"]["body"] = {"mode": "EXACT", "id": BODY_ID}
        item["dependencies"]["environment"] = {"mode": "EXACT", "id": ENVIRONMENT_ID}
        item["dependencies"]["frame"] = {"mode": "EXACT", "id": FRAME_ID}
        item["dependencies"]["temporal"] = {"mode": "NONE"}
        store.append_record(item)
        self.experience_id = item["experience_id"]
        exported = export_package_v2(source_state, self.a / "package-v2")

        # B: independent target Host deployment.
        self.b = self.root / "B"
        self.b.mkdir()
        self.package = self.b / "copied-package-v2"
        shutil.copytree(exported, self.package)
        self.runtime = self.package / "runtime"
        self.state = self.b / "restored_state"
        import_package_v2(self.package, self.state)
        self.host = self.b / "trusted_host"
        host_package = self.host / "phanes_host"
        host_package.mkdir(parents=True)
        for filename in ("__init__.py", "mock_uav.py", "p2_bootstrap.py"):
            shutil.copyfile(REPO_ROOT / "phanes_host" / filename, host_package / filename)
        self.installed_discovery = host_package / "_p0_discovery.py"
        shutil.copyfile(REPO_ROOT / "phanes" / "discovery.py", self.installed_discovery)

        self.before_self = {name: (self.state / name).read_bytes() for name in SELF_FILES_V2}
        self.before_package = {
            path.relative_to(self.package).as_posix(): path.read_bytes()
            for path in self.package.rglob("*") if path.is_file()
        }
        # The producer tree disappears before any child process runs.
        self.a_moved = self.root / "A_unavailable"
        self.a.rename(self.a_moved)

    def run_child(self, mode, *, experience_id=None):
        env = dict(os.environ)
        # Deliberately hostile inherited path: -I must ignore both the
        # development repository and the vanished deployment A.
        env["PYTHONPATH"] = os.pathsep.join((str(REPO_ROOT), str(self.a)))
        args = [
            sys.executable, "-I", "-S", "-B", "-c", CHILD_SCRIPT,
            str(self.runtime), str(self.host), str(REPO_ROOT), str(self.a), mode,
            str(self.package), str(self.state),
        ]
        if experience_id:
            args.append(experience_id)
        result = subprocess.run(args, cwd=self.b, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout)
        self.assert_deployment_isolation(evidence)
        return evidence, result.stderr

    def assert_deployment_isolation(self, evidence):
        self.assertNotEqual(evidence["pid"], os.getpid())
        self.assertEqual(evidence["sys_path"][:2], [str(self.runtime), str(self.host)])
        for entry in evidence["sys_path"]:
            resolved = Path(entry).resolve()
            self.assertFalse(resolved.is_relative_to(REPO_ROOT), entry)
            self.assertFalse(resolved.is_relative_to(self.a_moved), entry)
        self.assertFalse(self.a.exists())
        origins = evidence["origins"]
        for name in ("phanes", "capabilities", "p1_runtime"):
            self.assertTrue(Path(origins[name]).resolve().is_relative_to(self.runtime), name)
        for name in ("phanes_host", "p2_bootstrap", "mock_uav"):
            self.assertTrue(Path(origins[name]).resolve().is_relative_to(self.host), name)
        if "_p0_discovery" in origins:
            self.assertEqual(Path(origins["_p0_discovery"]), self.installed_discovery)
        # Onboarding never mutated the deployment bytes (onboarding modes only).
        if "self_hashes_before" in evidence:
            self.assertEqual(evidence["self_hashes_before"], evidence["self_hashes_after"])
            self.assertEqual(evidence["package_digest_before"], evidence["package_digest_after"])
        self.assertEqual(self.before_self, {name: (self.state / name).read_bytes() for name in SELF_FILES_V2})
        self.assertEqual(self.before_package, {
            path.relative_to(self.package).as_posix(): path.read_bytes()
            for path in self.package.rglob("*") if path.is_file()
        })
        validate_package_v2(self.package)

    def test_installed_host_tree_is_independent_of_repository(self):
        manifest = validate_package_v2(self.package)
        self.assertNotIn("runtime/phanes/discovery.py", manifest["files"])
        self.assertFalse(
            any("phanes_host" in name or "p2_bootstrap" in name for name in manifest["files"])
        )
        self.assertEqual(
            self.installed_discovery.read_bytes(), (REPO_ROOT / "phanes" / "discovery.py").read_bytes()
        )
        self.assertFalse((REPO_ROOT / "phanes_host" / "_p0_discovery.py").exists())
        restored = {name: (self.state / name).read_bytes() for name in SELF_FILES_V2}
        self.assertEqual(
            restored,
            {name: (self.a_moved / "state" / name).read_bytes() for name in SELF_FILES_V2},
        )

    def test_import_in_isolated_host_performs_no_onboarding(self):
        evidence, _ = self.run_child("import_only")
        self.assertEqual(set(evidence["counters"].values()), {0})
        self.assertEqual(set(evidence["provider_calls"].values()), {0})
        self.assertFalse(evidence["published"])
        self.assertEqual(evidence["module_descriptors"], [])
        self.assertTrue(evidence["discovery_unloaded"])

    def test_zero_authority_onboarding_has_no_executable_capability(self):
        evidence, _ = self.run_child("zero_authority", experience_id=self.experience_id)
        self.assertEqual(
            evidence["counters"],
            {"discovery": 1, "adapter_init": 1, "adapter_invoke": 0, "registry_init": 1,
             "registry_invoke": 0, "bind": 1, "compose": 1, "uuid": 1},
        )
        # Providers are invoked only during the request phase, never onboarding.
        self.assertEqual(evidence["provider_calls"], {"environment": 1, "frame": 1, "time": 1})
        session = evidence["session"]
        self.assertEqual(session["body_id"], BODY_ID)
        self.assertTrue(session["host_session_id"])
        self.assertTrue(session["host_session_id_is_uuid4"])
        self.assertEqual(session["offered"], ["get_position", "move_to"])
        self.assertEqual(session["authorized"], [])  # zero authority inherited nothing
        self.assertEqual(
            evidence["request"],
            {"kind": "invocation", "ok": False, "code": "CAPABILITY_UNAVAILABLE"},
        )

    def test_explicit_authority_exercises_real_p1_path_once(self):
        evidence, stderr = self.run_child("explicit_authority", experience_id=self.experience_id)
        self.assertEqual(
            evidence["counters"],
            {"discovery": 1, "adapter_init": 1, "adapter_invoke": 1, "registry_init": 1,
             "registry_invoke": 1, "bind": 1, "compose": 1, "uuid": 1},
        )
        self.assertEqual(evidence["provider_calls"], {"environment": 1, "frame": 1, "time": 1})
        session = evidence["session"]
        self.assertEqual(session["body_id"], BODY_ID)
        self.assertEqual(session["offered"], ["get_position", "move_to"])
        self.assertEqual(session["authorized"], ["move_to"])
        self.assertEqual(session["context_source_kinds"],
                         ["body_instance_id", "environment_id", "frame_id", "evaluation_time"])
        self.assertEqual(evidence["request"], {"kind": "invocation", "ok": True, "data": POSITION})
        # The original MockUAV adapter (not a wrapper stand-in) executed move_to.
        self.assertIn(f"[MockUAV] moving to ({POSITION['x']}, {POSITION['y']})", stderr)
        print("P2.3_EVIDENCE " + json.dumps(evidence, sort_keys=True))

    def test_missing_frame_context_is_unknown_without_invocation(self):
        evidence, _ = self.run_child("missing_context", experience_id=self.experience_id)
        self.assertEqual(
            evidence["counters"],
            {"discovery": 1, "adapter_init": 1, "adapter_invoke": 0, "registry_init": 1,
             "registry_invoke": 0, "bind": 1, "compose": 1, "uuid": 1},
        )
        self.assertEqual(evidence["provider_calls"], {"environment": 1, "frame": 1, "time": 1})
        self.assertEqual(
            evidence["request"],
            {"kind": "applicability", "status": "UNKNOWN", "reasons": ["FRAME_CONTEXT_MISSING"]},
        )

    def test_provider_exception_propagates_without_invocation(self):
        evidence, stderr = self.run_child("provider_exception", experience_id=self.experience_id)
        self.assertEqual(
            evidence["counters"],
            {"discovery": 1, "adapter_init": 1, "adapter_invoke": 0, "registry_init": 1,
             "registry_invoke": 0, "bind": 1, "compose": 1, "uuid": 1},
        )
        # snapshot order: body (registry), environment, frame (raised), time.
        self.assertEqual(evidence["provider_calls"], {"environment": 1, "frame": 1, "time": 0})
        self.assertEqual(
            evidence["request"],
            {"kind": "exception", "type": "RuntimeError",
             "message": "current frame provider failed", "is_onboarding_error": False},
        )
        self.assertNotIn("[MockUAV]", stderr)


if __name__ == "__main__":
    unittest.main()
