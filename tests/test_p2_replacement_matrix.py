"""P2.4 embodiment/session replacement matrix.

Every case runs fresh ``python -I -S -B`` subprocesses (this is not hot
rebind) against one migrated Self that carries historical Experiences from
two different Bodies. The matrix proves the distinction between Self
continuity (agent, memory, Experience history) and Host/session continuity
(body, authority, providers, current context, session ID), which is
observation-based evidence only: no cryptographic session-ID uniqueness is
claimed or testable here.
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

ENVIRONMENT_ID = "env-alpha"
FRAME_A = "frame-alpha"
FRAME_B = "frame-beta"
EXP1_POSITION = {"x": 10, "y": 20}
EXP2_POSITION = {"x": 30, "y": 40}

# One onboarding + optional navigation per fresh process. Empty frame means
# the provider returns None; empty allowed list means zero authority.
CHILD_SCRIPT = r'''
import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path

runtime_root, host_root, repo_root, old_a = map(Path, sys.argv[1:5])
package_dir, state_dir = map(Path, sys.argv[5:7])
body_id, allowed_csv, environment, frame, experience_id = sys.argv[7:12]

sys.path[:0] = [str(runtime_root), str(host_root)]

# --- deployment isolation (P2.3.2 rules re-checked in every matrix case) ---
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

import phanes.capabilities as capabilities
import phanes.p1_runtime as p1_runtime
import phanes_host
import phanes_host.mock_uav as mock_uav
from phanes.identity import load_identity
from phanes_host import p2_bootstrap

origins = {
    "phanes": __import__("phanes").__file__,
    "phanes_host": phanes_host.__file__,
    "p2_bootstrap": p2_bootstrap.__file__,
    "mock_uav": mock_uav.__file__,
}
for name, origin in origins.items():
    expected = runtime_root if name == "phanes" else host_root
    assert Path(origin).resolve().is_relative_to(expected), (name, origin)

counters = dict.fromkeys(
    ("discovery", "adapter_init", "adapter_invoke", "registry_init", "registry_invoke",
     "bind", "compose", "uuid"), 0)

import phanes_host._p0_discovery as host_discovery
original_discover = host_discovery.discover_body
def counted_discover(config):
    counters["discovery"] += 1
    return original_discover(config)
host_discovery.discover_body = counted_discover

original_adapter_init = mock_uav.MockUAVAdapter.__init__
def counted_adapter_init(self, bid):
    counters["adapter_init"] += 1
    original_adapter_init(self, bid)
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

# host_session_id must never appear in the request-scoped current context.
from phanes.experience_gateway import TargetContextSources
original_snapshot = TargetContextSources.snapshot
def counted_snapshot(self):
    context = original_snapshot(self)
    assert not hasattr(context, "host_session_id"), "session ID leaked into TargetContext"
    return context
TargetContextSources.snapshot = counted_snapshot

provider_calls = dict.fromkeys(("environment", "frame", "time"), 0)

def make_provider(key, value):
    def provider():
        provider_calls[key] += 1
        return value
    return provider

SELF_FILES = ("identity.json", "memory.json", "experiences.json")
self_hashes = {
    name: hashlib.sha256((state_dir / name).read_bytes()).hexdigest() for name in SELF_FILES
}

allowed = tuple(part for part in allowed_csv.split(",") if part)
config = {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": body_id}, "allowed_capabilities": []}
providers = dict(
    get_environment_id=make_provider("environment", environment or None),
    get_asserted_frame_id=make_provider("frame", frame or None),
    get_evaluation_time=make_provider("time", None),
)
descriptor, runtime = p2_bootstrap.onboard_host_session(state_dir, config, allowed_capabilities=allowed, **providers)
assert p2_bootstrap._published is True
assert all(call == 0 for call in provider_calls.values()), "providers called during onboarding"
assert counters["adapter_invoke"] == 0 and counters["registry_invoke"] == 0

session = {
    "agent_id": load_identity(state_dir).agent_id,
    "body_id": descriptor.body.body_id,
    "body_type": descriptor.body.body_type,
    "host_session_id": descriptor.host_session_id,
    "authorized": [spec.name for spec in bound[-1].list()],
    "explicit_authority": list(allowed),
}

request = None
if experience_id:
    with contextlib.redirect_stdout(sys.stderr):
        outcome = runtime.navigate_experience(experience_id)
    status = getattr(outcome, "status", None)
    if status is not None:
        reasons = list(outcome.reason_codes)
        assert descriptor.host_session_id not in reasons, "session ID leaked into applicability"
        request = {"kind": "applicability", "status": status.value, "reasons": reasons}
    elif outcome.ok:
        request = {"kind": "invocation", "ok": True, "data": outcome.data}
    else:
        request = {"kind": "invocation", "ok": False, "code": outcome.error.code}

evidence = {
    "pid": os.getpid(),
    "origins": origins,
    "counters": counters,
    "provider_calls": provider_calls,
    "session": session,
    "request": request,
    "self_hashes": self_hashes,
}
print(json.dumps(evidence, sort_keys=True))
'''


class TestP2ReplacementMatrix(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

        # One Self whose history contains experiences from two bodies.
        self.a = self.root / "A"
        source_state = self.a / "state"
        identity = init_identity(source_state)
        MemoryStore.create(source_state, identity.agent_id)
        store = ExperienceStore.create(source_state, identity.agent_id, test_mode=True)
        exp1 = record(agent_id=identity.agent_id)  # dock-a @ (10, 20) on UAV-1
        exp1["payload"]["position"] = dict(EXP1_POSITION)
        exp1["dependencies"]["body"] = {"mode": "EXACT", "id": "uav-1"}
        exp1["dependencies"]["environment"] = {"mode": "EXACT", "id": ENVIRONMENT_ID}
        exp1["dependencies"]["frame"] = {"mode": "EXACT", "id": FRAME_A}
        exp1["dependencies"]["temporal"] = {"mode": "NONE"}
        store.append_record(exp1)
        exp2 = record(agent_id=identity.agent_id)  # dock-b @ (30, 40) on UAV-2
        exp2["payload"]["place_id"] = "dock-b"
        exp2["payload"]["position"] = dict(EXP2_POSITION)
        exp2["dependencies"]["body"] = {"mode": "EXACT", "id": "uav-2"}
        exp2["dependencies"]["environment"] = {"mode": "EXACT", "id": ENVIRONMENT_ID}
        exp2["dependencies"]["frame"] = {"mode": "EXACT", "id": FRAME_B}
        exp2["dependencies"]["temporal"] = {"mode": "NONE"}
        store.append_record(exp2)
        self.agent_id = identity.agent_id
        self.exp1_id, self.exp2_id = exp1["experience_id"], exp2["experience_id"]
        exported = export_package_v2(source_state, self.a / "package-v2")

        self.b = self.root / "B"
        self.b.mkdir()
        self.package = self.b / "copied-package-v2"
        shutil.copytree(exported, self.package)
        self.runtime = self.package / "runtime"
        self.host = self.b / "trusted_host"
        host_package = self.host / "phanes_host"
        host_package.mkdir(parents=True)
        for filename in ("__init__.py", "mock_uav.py", "p2_bootstrap.py"):
            shutil.copyfile(REPO_ROOT / "phanes_host" / filename, host_package / filename)
        shutil.copyfile(REPO_ROOT / "phanes" / "discovery.py", host_package / "_p0_discovery.py")
        self.state = self.b / "restored_state"
        import_package_v2(self.package, self.state)

        self.before_self = {name: (self.state / name).read_bytes() for name in SELF_FILES_V2}
        self.before_package = {
            path.relative_to(self.package).as_posix(): path.read_bytes()
            for path in self.package.rglob("*") if path.is_file()
        }
        self.a_unavailable = self.root / "A_unavailable"
        self.a.rename(self.a_unavailable)

    def run_case(self, case, *, body_id, allowed=(), environment=ENVIRONMENT_ID, frame=FRAME_A,
                 experience_id=None):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join((str(REPO_ROOT), str(self.a)))
        args = [
            sys.executable, "-I", "-S", "-B", "-c", CHILD_SCRIPT,
            str(self.runtime), str(self.host), str(REPO_ROOT), str(self.a),
            str(self.package), str(self.state),
            body_id, ",".join(allowed), environment, frame, experience_id or "",
        ]
        result = subprocess.run(args, cwd=self.b, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout)
        # Deployment bytes never change, whatever the case does.
        self.assertEqual(self.before_self, {name: (self.state / name).read_bytes() for name in SELF_FILES_V2})
        self.assertEqual(self.before_package, {
            path.relative_to(self.package).as_posix(): path.read_bytes()
            for path in self.package.rglob("*") if path.is_file()
        })
        validate_package_v2(self.package)
        self.assertNotEqual(evidence["session"]["agent_id"], "")
        self.assertEqual(evidence["session"]["agent_id"], self.agent_id)
        self.assertEqual(evidence["counters"]["bind"], 1)  # fresh Registry per process
        row = {
            "case": case,
            "pid": evidence["pid"],
            "agent_id": evidence["session"]["agent_id"],
            "body_id": evidence["session"]["body_id"],
            "session_id": evidence["session"]["host_session_id"],
            "authority": evidence["session"]["authorized"],
            "current_context": {"environment": environment or None, "frame": frame or None},
            "result": evidence["request"],
            "registry_invoke": evidence["counters"]["registry_invoke"],
            "adapter_invoke": evidence["counters"]["adapter_invoke"],
            "self_hashes": evidence["self_hashes"],
        }
        print("P2.4_MATRIX " + json.dumps(row, sort_keys=True))
        return evidence

    def test_case_a_same_self_same_body_new_process_new_session(self):
        first = self.run_case("A", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        second = self.run_case("A", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        self.assertNotEqual(first["pid"], second["pid"])
        self.assertEqual(first["session"]["agent_id"], second["session"]["agent_id"])
        self.assertEqual(first["session"]["body_id"], second["session"]["body_id"])
        # Observed inequality of ephemeral IDs only; no cryptographic claim.
        self.assertNotEqual(first["session"]["host_session_id"], second["session"]["host_session_id"])
        self.assertEqual(first["self_hashes"], second["self_hashes"])
        for evidence in (first, second):
            self.assertEqual(evidence["counters"]["registry_init"], 1)
            self.assertEqual(evidence["counters"]["bind"], 1)
            self.assertEqual(evidence["provider_calls"], {"environment": 1, "frame": 1, "time": 1})
            self.assertEqual(evidence["request"], {"kind": "invocation", "ok": True, "data": EXP1_POSITION})

    def test_case_b_same_self_different_body_same_type(self):
        before = self.run_case("B-before", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        after = self.run_case("B", body_id="uav-2", allowed=("move_to",), experience_id=self.exp1_id)
        self.assertEqual(before["session"]["body_type"], after["session"]["body_type"])
        self.assertNotEqual(before["session"]["body_id"], after["session"]["body_id"])
        self.assertEqual(after["session"]["body_id"], "uav-2")
        self.assertNotEqual(before["session"]["host_session_id"], after["session"]["host_session_id"])
        # The historical experience stays; it simply no longer matches the new body.
        self.assertEqual(
            after["request"],
            {"kind": "applicability", "status": "INAPPLICABLE", "reasons": ["BODY_MISMATCH"]},
        )
        self.assertEqual((after["counters"]["registry_invoke"], after["counters"]["adapter_invoke"]), (0, 0))

    def test_case_c_same_self_different_body_different_authority(self):
        before = self.run_case("C-before", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        after = self.run_case("C", body_id="uav-2", allowed=("get_position",),
                              environment=ENVIRONMENT_ID, frame=FRAME_B, experience_id=self.exp2_id)
        self.assertNotEqual(before["session"]["authorized"], after["session"]["authorized"])
        self.assertEqual(after["session"]["authorized"], ["get_position"])
        # Experience is fully applicable, but move_to was never authorized here.
        self.assertEqual(
            after["request"],
            {"kind": "invocation", "ok": False, "code": "CAPABILITY_UNAVAILABLE"},
        )
        self.assertEqual((after["counters"]["registry_invoke"], after["counters"]["adapter_invoke"]), (0, 0))

    def test_case_d_same_experience_different_current_context(self):
        original = self.run_case("D-original", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        self.assertEqual(original["request"], {"kind": "invocation", "ok": True, "data": EXP1_POSITION})
        missing = self.run_case("D-frame-missing", body_id="uav-1", allowed=("move_to",),
                                frame="", experience_id=self.exp1_id)
        self.assertEqual(
            missing["request"],
            {"kind": "applicability", "status": "UNKNOWN", "reasons": ["FRAME_CONTEXT_MISSING"]},
        )
        mismatched = self.run_case("D-frame-changed", body_id="uav-1", allowed=("move_to",),
                                   frame=FRAME_B, experience_id=self.exp1_id)
        self.assertEqual(
            mismatched["request"],
            {"kind": "applicability", "status": "INAPPLICABLE", "reasons": ["FRAME_MISMATCH"]},
        )
        for evidence in (missing, mismatched):
            self.assertEqual((evidence["counters"]["registry_invoke"], evidence["counters"]["adapter_invoke"]), (0, 0))

    def test_case_e_new_body_zero_authority_after_authorized_history(self):
        before = self.run_case("E-before", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        self.assertEqual(before["session"]["authorized"], ["move_to"])
        self.assertEqual(before["request"], {"kind": "invocation", "ok": True, "data": EXP1_POSITION})
        after = self.run_case("E", body_id="uav-2", allowed=(),
                              environment=ENVIRONMENT_ID, frame=FRAME_B, experience_id=self.exp2_id)
        self.assertEqual(after["session"]["authorized"], [])
        # History is found and applicable; the zero-authority session cannot act.
        self.assertEqual(
            after["request"],
            {"kind": "invocation", "ok": False, "code": "CAPABILITY_UNAVAILABLE"},
        )
        self.assertEqual((after["counters"]["registry_invoke"], after["counters"]["adapter_invoke"]), (0, 0))

    def test_case_f_new_body_explicit_compatible_authority(self):
        evidence = self.run_case("F", body_id="uav-2", allowed=("move_to",),
                                 environment=ENVIRONMENT_ID, frame=FRAME_B, experience_id=self.exp2_id)
        self.assertEqual(evidence["session"]["authorized"], ["move_to"])
        self.assertEqual(evidence["request"], {"kind": "invocation", "ok": True, "data": EXP2_POSITION})
        self.assertEqual((evidence["counters"]["registry_invoke"], evidence["counters"]["adapter_invoke"]), (1, 1))

    def test_authority_non_continuity_blocks_navigation_without_hiding_history(self):
        self.run_case("authority-A", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        zero = self.run_case("authority-B-zero", body_id="uav-1", allowed=(), experience_id=self.exp1_id)
        self.assertEqual(zero["session"]["authorized"], [])
        self.assertEqual(
            zero["request"],
            {"kind": "invocation", "ok": False, "code": "CAPABILITY_UNAVAILABLE"},
        )
        self.assertEqual(zero["provider_calls"], {"environment": 1, "frame": 1, "time": 1})
        self.assertEqual(zero["counters"]["adapter_invoke"], 0)

    def test_body_replacement_persists_self_and_replaces_host_state(self):
        process_a = self.run_case("replacement-A", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        self.assertEqual(process_a["request"], {"kind": "invocation", "ok": True, "data": EXP1_POSITION})
        # Real migration of the unchanged Self to a second restored deployment.
        second_state = self.b / "restored_state_2"
        import_package_v2(self.package, second_state)
        self.assertEqual(self.before_self, {name: (second_state / name).read_bytes() for name in SELF_FILES_V2})

        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join((str(REPO_ROOT), str(self.a)))

        def run(experience_id):
            args = [
                sys.executable, "-I", "-S", "-B", "-c", CHILD_SCRIPT,
                str(self.runtime), str(self.host), str(REPO_ROOT), str(self.a),
                str(self.package), str(second_state),
                "uav-2", "move_to", ENVIRONMENT_ID, FRAME_B, experience_id,
            ]
            result = subprocess.run(args, cwd=self.b, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        # History carried over: exp1 is found but its body/context are absent here.
        old_body = run(self.exp1_id)
        self.assertEqual(
            old_body["request"],
            {"kind": "applicability", "status": "INAPPLICABLE",
             "reasons": ["BODY_MISMATCH", "FRAME_MISMATCH"]},
        )
        self.assertEqual(old_body["counters"]["adapter_invoke"], 0)  # Adapter A absent
        # The new body with its own authority and current context works.
        new_body = run(self.exp2_id)
        self.assertEqual(new_body["request"], {"kind": "invocation", "ok": True, "data": EXP2_POSITION})
        self.assertEqual(new_body["session"]["agent_id"], self.agent_id)
        self.assertEqual(new_body["session"]["body_id"], "uav-2")
        self.assertNotEqual(new_body["session"]["host_session_id"], process_a["session"]["host_session_id"])
        self.assertEqual(new_body["self_hashes"], process_a["self_hashes"])  # Self continuity

    def test_session_id_is_non_semantic_across_processes(self):
        first = self.run_case("session-1", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        second = self.run_case("session-2", body_id="uav-1", allowed=("move_to",), experience_id=self.exp1_id)
        self.assertNotEqual(first["session"]["host_session_id"], second["session"]["host_session_id"])
        self.assertEqual(first["request"], second["request"])
        self.assertEqual(first["provider_calls"], second["provider_calls"])
        self.assertEqual(first["counters"], second["counters"])
        self.assertEqual(first["self_hashes"], second["self_hashes"])


if __name__ == "__main__":
    unittest.main()
