"""P1.7 acceptance: Self plus generic Runtime cross a real A/B process boundary.

The scripts are test harnesses, not package content or a new production API.
Each B case starts a fresh isolated interpreter with only the copied v2 Runtime
and B-local trusted Host on its import path. The B importer is a distinct,
preinstalled trusted copy of the migration tool; it never executes package code.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parent.parent
SELF_NAMES = ("identity.json", "memory.json", "experiences.json")
RUNTIME_NAMES = (
    "__init__.py", "contracts.py", "capabilities.py", "identity.py", "memory.py",
    "experience_contracts.py", "experience_validation.py", "experience_semantic.py",
    "experience_store.py", "applicability.py", "experience_gateway.py",
    "experience_core.py", "p1_runtime.py",
)

# Acceptance specification: the fixed set of scenario names the B gate
# matrix must execute. Deliberately independent of the case table — it is
# never derived from it, so deleting a scenario from the matrix makes the
# acceptance test fail instead of silently shrinking the evidence.
REQUIRED_SCENARIOS = frozenset({
    "no_body",
    "zero_auth",
    "alpha_1",
    "alpha_2",
    "old",
    "new",
    "derived_parent",
    "derived_child",
    "body_mismatch",
    "body_missing",
    "environment_other",
    "environment_missing",
    "frame_other",
    "frame_unasserted",
    "time_match",
    "time_before",
    "time_expired",
    "time_missing",
    "temporal_unknown",
    "missing",
    "legacy_name",
    "bad_history",
    "evaluator_failure",
    "provider_exception",
    "parameter_navigation",
    "huge",
})

A_SCRIPT = r'''
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from phanes.capabilities import CapabilityRegistry
from phanes.contracts import MOVE_TO
from phanes.experience_store import ExperienceStore
from phanes.identity import init_identity
from phanes.memory import MemoryStore
from phanes.migration_v2 import export_package_v2
from phanes_host.mock_uav import MockUAVAdapter

state, package = Path(sys.argv[2]), Path(sys.argv[3])
identity = init_identity(state)
memory = MemoryStore.create(state, identity.agent_id)
memory.remember_place("Legacy", 777, 888)
store = ExperienceStore.create(state, identity.agent_id, test_mode=True)

def make(place, x, y, *, environment="env-match", body=None, temporal=None):
    return {
        "experience_id": str(uuid.uuid4()), "agent_id": identity.agent_id,
        "kind": "place_observation",
        "payload": {"place_id": place, "position": {"x": x, "y": y}},
        "provenance": {"source_class": "test_fixture", "source_ref": "test:p1.7"},
        "dependencies": {
            "body": {"mode": "EXACT", "id": body} if body else {"mode": "NONE"},
            "environment": {"mode": "EXACT", "id": environment},
            "frame": {"mode": "EXACT", "id": "mock-map-1"},
            "temporal": temporal or {"mode": "NONE"},
        },
        "recorded_at": "2026-09-28T00:00:00Z", "observed_at": None,
        "derived_from": [], "supersedes": None,
    }

items = {
    "alpha_1": make("Alpha", 10, 20),
    "alpha_2": make("Alpha", 90, 90),
    "old": make("Corrected", 30, 40),
    "parent": make("Parent", 41, 42),
    "body_exact": make("BodyOnly", 12, 13, body="body-a"),
    "interval": make("Timed", 21, 22, temporal={
        "mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z",
        "valid_until": "2026-09-30T00:00:00Z"}),
    "temporal_unknown": make("UnknownTime", 23, 24, temporal={"mode": "UNKNOWN"}),
    "huge": make("Huge", 10**400, -(10**400)),
}
items["new"] = make("Corrected", 50, 60)
items["new"]["supersedes"] = items["old"]["experience_id"]
items["child"] = make("Child", 43, 44, environment="env-child")
items["child"]["derived_from"] = [items["parent"]["experience_id"]]
items["parameter"] = {
    "experience_id": str(uuid.uuid4()), "agent_id": identity.agent_id,
    "kind": "body_parameter_observation",
    "payload": {"parameter_name": "ROLL_KP", "value": 0.18, "unit": "mock-unit"},
    "provenance": {"source_class": "test_fixture", "source_ref": "test:p1.7"},
    "dependencies": {
        "body": {"mode": "EXACT", "id": "body-a"},
        "environment": {"mode": "NONE"}, "frame": {"mode": "NONE"},
        "temporal": {"mode": "NONE"},
    },
    "recorded_at": "2026-09-28T00:00:00Z", "observed_at": None,
    "derived_from": [], "supersedes": None,
}
for item in items.values():
    store.append_record(item)

# A's current Body and grant exist only in this process, outside Self.
registry = CapabilityRegistry()
adapter = MockUAVAdapter("body-a")
registry.bind(adapter, [MOVE_TO])
assert registry.invoke(MOVE_TO, {"x": 31, "y": 47}).ok
assert (adapter._x, adapter._y) == (31, 47)
export_package_v2(state, package)
print(json.dumps({
    "pid": os.getpid(), "agent_id": identity.agent_id,
    "ids": {name: item["experience_id"] for name, item in items.items()},
    "position": [adapter._x, adapter._y], "record_count": len(items),
}))
'''

B_IMPORT_SCRIPT = r'''
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

tool_root, package, state = map(Path, sys.argv[1:4])
sys.path.insert(0, str(tool_root))
import phanes
from phanes.applicability import evaluate_experience_use
from phanes.capabilities import CapabilityRegistry
from phanes.experience_gateway import ExperienceUseGateway
from phanes.migration_v2 import import_package_v2
from phanes_host.mock_uav import MockUAVAdapter

# Import may validate inert data; no evaluator, Gateway, Registry or Adapter
# invocation is allowed. The B trusted tool is not the transported Runtime.
with (patch("phanes.applicability.evaluate_experience_use", side_effect=AssertionError("evaluation during import")) as evaluated,
      patch.object(ExperienceUseGateway, "request", side_effect=AssertionError("Gateway during import")) as gateway,
      patch.object(CapabilityRegistry, "bind", side_effect=AssertionError("Registry.bind during import")) as bound,
      patch.object(CapabilityRegistry, "list", side_effect=AssertionError("Registry.list during import")) as listed,
      patch.object(CapabilityRegistry, "invoke", side_effect=AssertionError("Registry.invoke during import")) as invoked,
      patch.object(MockUAVAdapter, "__init__", side_effect=AssertionError("Adapter construction during import")) as constructed,
      patch.object(MockUAVAdapter, "invoke", side_effect=AssertionError("Adapter during import")) as adapted):
    identity = import_package_v2(package, state)
counts = [evaluated.call_count, gateway.call_count, bound.call_count,
          listed.call_count, invoked.call_count, constructed.call_count,
          adapted.call_count]
print(json.dumps({"pid": os.getpid(), "agent_id": identity.agent_id,
                  "counts": counts, "tool_origin": str(Path(phanes.__file__).resolve()),
                  "package_runtime_loaded": any(
                      str(package / "runtime") in str(getattr(module, "__file__", ""))
                      for module in sys.modules.values())}))
'''

B_RUN_SCRIPT = r'''
import json
import os
import sys
import uuid
import contextlib
from pathlib import Path
from unittest.mock import patch

runtime_root, host_root, state = map(Path, sys.argv[1:4])
request = json.loads(sys.argv[4])
sys.path.insert(0, str(runtime_root))
sys.path.insert(1, str(host_root))

import phanes
import phanes_host.mock_uav as host_module
import phanes.experience_gateway as gateway_module
from phanes.applicability import ApplicabilityResult, EvaluatorFailure, IntendedUse
from phanes.capabilities import CapabilityRegistry
from phanes.contracts import GET_POSITION, MOVE_TO
from phanes.experience_contracts import EVALUATOR_INTERNAL_ERROR
from phanes.experience_gateway import HistoricalExperienceResult
from phanes.identity import load_identity
from phanes.memory import MemoryStore
from phanes.p1_runtime import compose_p1_runtime
from phanes_host.mock_uav import MockUAVAdapter

identity = load_identity(state)
memory = MemoryStore.load(state, identity.agent_id)
legacy = memory.lookup_place("Legacy")
registry = CapabilityRegistry()
adapter = None
if request.get("body", True):
    adapter = MockUAVAdapter("body-b")
    registry.bind(adapter, [MOVE_TO] if request.get("authorize", False) else [])

current = {"environment": "env-match", "frame": "mock-map-1",
           "frame_asserted": adapter is not None, "time": "2026-09-29T00:00:00Z"}
provider_calls = {"environment": 0, "frame": 0, "time": 0}
def environment():
    provider_calls["environment"] += 1
    return current["environment"]
def frame():
    provider_calls["frame"] += 1
    if current.get("provider_error"):
        raise RuntimeError("B frame provider failed")
    # B-local Host/operator assertion: this newly bound MockUAV has origin
    # (0,0), x/y axes, mock unit, and move_to(x,y) interpretation declared
    # for this frame. A's historical frame string is never a current source.
    return current["frame"] if current["frame_asserted"] else None
def clock():
    provider_calls["time"] += 1
    return current["time"]

runtime = compose_p1_runtime(
    state, registry, get_environment_id=environment,
    get_asserted_frame_id=frame, get_evaluation_time=clock)

# Observe the freshly created B Adapter's actual runtime position through
# the accepted read-only GET_POSITION capability: after the Adapter exists
# and after composition, but before any navigation operation. This is a
# real observation of B's new Body state, never a preset constant and never
# derived from A's state, the Experience content or fixture metadata.
position_before = None
if adapter is not None:
    observed = adapter.invoke(GET_POSITION, {})
    position_before = [observed["x"], observed["y"]]

def encode(value):
    if isinstance(value, ApplicabilityResult):
        return {"kind": "applicability", "status": value.status.value,
                "reasons": list(value.reason_codes)}
    if isinstance(value, EvaluatorFailure):
        return {"kind": "failure", "code": value.error_code}
    if isinstance(value, HistoricalExperienceResult):
        return {"kind": "historical", "record_id": value.record["experience_id"],
                "record_kind": value.record["kind"]}
    return {"kind": "invocation", "ok": value.ok,
            "data": value.data if value.ok else None,
            "code": None if value.ok else value.error.code}

results = []
for step in request["steps"]:
    current.update(step.get("context", {}))
    before = dict(provider_calls)
    experience_id = step.get("experience_id", request["ids"].get(step.get("id")))
    if step.get("id") == "missing":
        experience_id = str(uuid.UUID("00000000-0000-4000-8000-000000000001"))
    if step.get("id") == "legacy_name":
        experience_id = "Legacy"
    snapshot_patch = None
    if step.get("bad_history"):
        invalid = runtime._gateway._store.document_snapshot()
        invalid["records"][0]["dependencies"]["frame"] = {"mode": "NONE"}
        snapshot_patch = patch.object(runtime._gateway._store, "document_snapshot", return_value=invalid)
    failure_patch = None
    if step.get("evaluator_failure"):
        failure_patch = patch.object(gateway_module, "evaluate_experience_use",
                              return_value=EvaluatorFailure(EVALUATOR_INTERNAL_ERROR, "injected"))
    try:
        if snapshot_patch:
            snapshot_patch.start()
        if failure_patch:
            failure_patch.start()
        with (patch.object(registry, "list", wraps=registry.list) as listed,
              patch.object(registry, "invoke", wraps=registry.invoke) as invoked,
              patch.object(adapter, "invoke", wraps=adapter.invoke) if adapter else patch.object(registry, "bind") as adapted,
              patch.object(runtime._gateway, "request", wraps=runtime._gateway.request) as gateway,
              patch.object(MemoryStore, "lookup_place", side_effect=AssertionError("legacy lookup bypass"))
                  if step.get("id") == "legacy_name" else contextlib.nullcontext()):
            try:
                if step.get("historical"):
                    outcome = runtime._gateway.request(experience_id, IntendedUse.HISTORICAL_QUERY)
                else:
                    outcome = runtime.navigate_experience(experience_id)
                result = encode(outcome)
            except Exception as exc:
                result = {"kind": "exception", "type": type(exc).__name__, "message": str(exc)}
            counts = [listed.call_count, invoked.call_count,
                      adapted.call_count if adapter else 0]
            gateway_count = gateway.call_count
    finally:
        if failure_patch:
            failure_patch.stop()
        if snapshot_patch:
            snapshot_patch.stop()
    results.append({"id": step.get("id"), "scenario": step.get("scenario", step.get("id")),
                    "result": result, "counts": counts,
                    "gateway_count": gateway_count,
                    "provider_delta": {key: provider_calls[key] - before[key] for key in before},
                    "position": [adapter._x, adapter._y] if adapter else None,
                    "context": dict(current)})

generic_origins = {
    name: str(Path(module.__file__).resolve())
    for name, module in sys.modules.items()
    if (name == "phanes" or name.startswith("phanes.")) and getattr(module, "__file__", None)
}
print(json.dumps({
    "pid": os.getpid(), "cwd": str(Path.cwd().resolve()), "sys_path": sys.path,
    "runtime_origin": str(Path(phanes.__file__).resolve()),
    "generic_origins": generic_origins,
    "host_origin": str(Path(host_module.__file__).resolve()),
    "body_id": adapter.descriptor.body_id if adapter else None,
    "authorized": request.get("authorize", False),
    "legacy": [legacy.x, legacy.y],
    "position_before": position_before,
    "results": results,
}))
'''


class P1IsolatedABAcceptance(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.a = self.root / "A"
        self.b = self.root / "B"
        self.state_a = self.a / "self"
        self.export_a = self.a / "package-v2"
        self.copy_b = self.b / "copied-package-v2"
        self.state_b = self.b / "self"
        self.tool_b = self.b / "trusted-tool"
        self.host_b = self.b / "trusted-host"
        self.runtime_b = self.copy_b / "runtime"
        self.work_b = self.b / "work"
        self.work_b.mkdir(parents=True)
        self._origins_reported = False
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(REPO_ROOT / "phanes", self.tool_b / "phanes", ignore=ignore)
        shutil.copytree(REPO_ROOT / "phanes_host", self.tool_b / "phanes_host", ignore=ignore)
        shutil.copytree(REPO_ROOT / "phanes_host", self.host_b / "phanes_host", ignore=ignore)

    def run_child(self, script, args, cwd):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", script, *map(str, args)],
            cwd=cwd, env=env, text=True, encoding="utf-8",
            capture_output=True, check=False, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + "\n" + proc.stdout)
        return json.loads(proc.stdout.splitlines()[-1])

    def prepare(self):
        self.a.mkdir()
        a_result = self.run_child(A_SCRIPT, (REPO_ROOT, self.state_a, self.export_a), self.a)
        before = {name: (self.state_a / name).read_bytes() for name in SELF_NAMES}
        self.assertEqual(a_result["position"], [31, 47])
        self.assertEqual(a_result["record_count"], 11)
        shutil.copytree(self.export_a, self.copy_b)
        self.assertNotEqual(self.export_a.resolve(), self.copy_b.resolve())
        manifest = json.loads((self.copy_b / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["package_version"], manifest["core_version"]), (2, "0.2"))
        self.assertEqual(set(manifest["files"]),
                         {f"self/{name}" for name in SELF_NAMES} |
                         {f"runtime/phanes/{name}" for name in RUNTIME_NAMES})
        self.assertFalse((self.copy_b / "runtime" / "phanes_host").exists())
        self.assertFalse(any((self.runtime_b / "phanes" / name).exists()
                             for name in ("__main__.py", "core.py", "discovery.py", "migration.py", "migration_v2.py")))
        imported = self.run_child(B_IMPORT_SCRIPT, (self.tool_b, self.copy_b, self.state_b), self.work_b)
        self.assertNotEqual(a_result["pid"], imported["pid"])
        self.assertEqual(imported["agent_id"], a_result["agent_id"])
        self.assertEqual(imported["counts"], [0, 0, 0, 0, 0, 0, 0])
        self.assertFalse(imported["package_runtime_loaded"])
        self.assertTrue(Path(imported["tool_origin"]).is_relative_to(self.tool_b))
        for name in SELF_NAMES:
            self.assertEqual((self.copy_b / "self" / name).read_bytes(), before[name])
            self.assertEqual((self.state_b / name).read_bytes(), before[name])
        # A is a disposable test fixture. Its entire state and export are
        # removed before any B operational child starts; no real user data.
        self.assertTrue(self.a.is_relative_to(self.root))
        shutil.rmtree(self.a)
        self.assertFalse(self.state_a.exists())
        self.assertFalse(self.export_a.exists())
        print("P1.7_PATHS " + json.dumps({
            "a_pid": a_result["pid"], "import_pid": imported["pid"],
            "a_self": str(self.state_a), "a_export": str(self.export_a),
            "b_copy": str(self.copy_b), "b_self": str(self.state_b),
            "b_runtime": str(self.runtime_b), "b_host": str(self.host_b),
            "self_bytes_equal": True, "import_counts": imported["counts"],
        }))
        return a_result, before

    def run_b(self, a_result, *, body=True, authorize=False, steps):
        request = {"ids": a_result["ids"], "body": body,
                   "authorize": authorize, "steps": steps}
        result = self.run_child(
            B_RUN_SCRIPT,
            (self.runtime_b, self.host_b, self.state_b, json.dumps(request)),
            self.work_b,
        )
        self.assertNotEqual(result["pid"], a_result["pid"])
        self.assertEqual(Path(result["cwd"]), self.work_b)
        self.assertEqual(result["legacy"], [777, 888])
        # B's initial Body position is an observed runtime fact reported by
        # the child, not a preset constant: a fresh B Adapter starts at the
        # origin and never at A's pre-export position (31, 47).
        if body:
            self.assertEqual(result["position_before"], [0, 0])
            self.assertNotEqual(result["position_before"], [31, 47])
        else:
            self.assertIsNone(result["position_before"])
        # Result-completeness: B must return exactly one result per sent
        # step — no missing, duplicate or unexpected scenario results.
        sent_names = [step.get("scenario", step.get("id")) for step in steps]
        returned_names = [step["scenario"] for step in result["results"]]
        self.assertEqual(len(result["results"]), len(steps))
        self.assertEqual(len(returned_names), len(set(returned_names)),
                         f"duplicate scenario results: {returned_names}")
        self.assertEqual(set(returned_names), set(sent_names),
                         f"sent {sent_names} but got {returned_names}")
        self.assertTrue(Path(result["runtime_origin"]).is_relative_to(self.runtime_b))
        self.assertTrue(Path(result["host_origin"]).is_relative_to(self.host_b))
        self.assertNotIn("phanes_host", result["generic_origins"])
        self.assertTrue({"phanes"} | {"phanes." + name.removesuffix(".py") for name in RUNTIME_NAMES if name != "__init__.py"}
                        <= set(result["generic_origins"]))
        for name, origin in result["generic_origins"].items():
            self.assertTrue(Path(origin).is_relative_to(self.runtime_b), (name, origin))
        for entry in result["sys_path"]:
            path = Path(entry or result["cwd"]).resolve()
            self.assertFalse(path.is_relative_to(REPO_ROOT), entry)
            self.assertFalse(path.is_relative_to(self.a), entry)
            self.assertFalse(path.is_relative_to(self.tool_b), entry)
        if not self._origins_reported:
            print("P1.7_ORIGINS " + json.dumps({
                "b_pid": result["pid"], "cwd": result["cwd"],
                "sys_path": result["sys_path"],
                "generic_origins": result["generic_origins"],
                "host_origin": result["host_origin"],
            }))
            self._origins_reported = True
        for step in result["results"]:
            print("P1.7_CASE " + json.dumps({
                "scenario": step["scenario"], "a_pid": a_result["pid"], "b_pid": result["pid"],
                "runtime_origin": result["runtime_origin"], "host_origin": result["host_origin"],
                "body_id": result["body_id"], "environment": step["context"]["environment"],
                "frame": step["context"]["frame"] if step["context"]["frame_asserted"] else None,
                "authorization": result["authorized"], "result": step["result"],
                "list_invoke_adapter": step["counts"], "position": step["position"],
            }))
        return result["results"]

    def test_isolated_v2_transport_and_fresh_b_gate_matrix(self):
        a_result, original_self = self.prepare()
        ids = a_result["ids"]
        cases = (
            # name, selected ID, body, authorization, B context, result, counts, final position
            ("no_body", "alpha_1", False, False, {}, ("UNKNOWN", "FRAME_CONTEXT_MISSING"), [0, 0, 0], None),
            ("zero_auth", "alpha_1", True, False, {}, ("invocation", "CAPABILITY_UNAVAILABLE"), [1, 0, 0], [0, 0]),
            ("alpha_1", "alpha_1", True, True, {}, ("success", [10, 20]), [1, 1, 1], [10, 20]),
            ("alpha_2", "alpha_2", True, True, {}, ("success", [90, 90]), [1, 1, 1], [90, 90]),
            ("old", "old", True, True, {}, ("INAPPLICABLE", "RECORD_SUPERSEDED"), [0, 0, 0], [0, 0]),
            ("new", "new", True, True, {}, ("success", [50, 60]), [1, 1, 1], [50, 60]),
            ("derived_parent", "parent", True, True, {}, ("success", [41, 42]), [1, 1, 1], [41, 42]),
            ("derived_child", "child", True, True, {}, ("INAPPLICABLE", "ENVIRONMENT_MISMATCH"), [0, 0, 0], [0, 0]),
            ("body_mismatch", "body_exact", True, True, {}, ("INAPPLICABLE", "BODY_MISMATCH"), [0, 0, 0], [0, 0]),
            ("body_missing", "body_exact", False, True, {}, ("UNKNOWN", "BODY_CONTEXT_MISSING"), [0, 0, 0], None),
            ("environment_other", "alpha_1", True, True, {"environment": "env-other"}, ("INAPPLICABLE", "ENVIRONMENT_MISMATCH"), [0, 0, 0], [0, 0]),
            ("environment_missing", "alpha_1", True, True, {"environment": None}, ("UNKNOWN", "ENVIRONMENT_CONTEXT_MISSING"), [0, 0, 0], [0, 0]),
            ("frame_other", "alpha_1", True, True, {"frame": "mock-map-other"}, ("INAPPLICABLE", "FRAME_MISMATCH"), [0, 0, 0], [0, 0]),
            ("frame_unasserted", "alpha_1", True, True, {"frame_asserted": False}, ("UNKNOWN", "FRAME_CONTEXT_MISSING"), [0, 0, 0], [0, 0]),
            ("time_match", "interval", True, True, {}, ("success", [21, 22]), [1, 1, 1], [21, 22]),
            ("time_before", "interval", True, True, {"time": "2026-09-27T00:00:00Z"}, ("INAPPLICABLE", "TEMPORALLY_NOT_YET_VALID"), [0, 0, 0], [0, 0]),
            ("time_expired", "interval", True, True, {"time": "2026-09-30T00:00:00Z"}, ("INAPPLICABLE", "TEMPORALLY_EXPIRED"), [0, 0, 0], [0, 0]),
            ("time_missing", "interval", True, True, {"time": None}, ("UNKNOWN", "EVALUATION_TIME_MISSING"), [0, 0, 0], [0, 0]),
            ("temporal_unknown", "temporal_unknown", True, True, {}, ("UNKNOWN", "TEMPORAL_DEPENDENCY_UNKNOWN"), [0, 0, 0], [0, 0]),
            ("missing", "missing", True, True, {}, ("failure", "EXPERIENCE_NOT_FOUND"), [0, 0, 0], [0, 0]),
            ("legacy_name", "legacy_name", True, True, {}, ("failure", "EXPERIENCE_NOT_FOUND"), [0, 0, 0], [0, 0]),
            ("bad_history", "alpha_1", True, True, {}, ("failure", "INVALID_EXPERIENCE"), [0, 0, 0], [0, 0]),
            ("evaluator_failure", "alpha_1", True, True, {}, ("failure", "EVALUATOR_INTERNAL_ERROR"), [0, 0, 0], [0, 0]),
            ("provider_exception", "alpha_1", True, True, {"provider_error": True}, ("exception", "RuntimeError"), [0, 0, 0], [0, 0]),
            ("parameter_navigation", "parameter", True, True, {}, ("INAPPLICABLE", "INTENDED_USE_NOT_ALLOWED"), [0, 0, 0], [0, 0]),
            ("huge", "huge", True, True, {}, ("success", [10**400, -(10**400)]), [1, 1, 1], [10**400, -(10**400)]),
        )
        # Scenario completeness gate, independent of result verification:
        # the case table must match the REQUIRED_SCENARIOS specification
        # exactly — a deleted, renamed or duplicated scenario fails the
        # acceptance test even if every remaining scenario passes. The
        # categorized mismatch check runs before the numeric cardinality
        # assertion so a missing, duplicated or unexpected scenario is
        # diagnosed by category, not masked by a bare count difference.
        self.assertEqual(len(REQUIRED_SCENARIOS), 26)
        case_names = [case[0] for case in cases]
        duplicates = sorted(
            name for name, count in Counter(case_names).items() if count > 1
        )
        missing = sorted(REQUIRED_SCENARIOS - set(case_names))
        unexpected = sorted(set(case_names) - REQUIRED_SCENARIOS)
        self.assertFalse(
            missing or unexpected or duplicates,
            "scenario completeness mismatch: "
            f"missing={missing}, unexpected={unexpected}, duplicate={duplicates}",
        )
        self.assertEqual(len(cases), 26)
        for name, selected, body, authorize, context, expected, counts, position in cases:
            with self.subTest(name=name):
                step = {"id": selected, "scenario": name, "context": context}
                if name in ("bad_history", "evaluator_failure"):
                    step[name] = True
                actual = self.run_b(a_result, body=body, authorize=authorize, steps=[step])[0]
                result = actual["result"]
                if expected[0] == "success":
                    self.assertEqual(result, {"kind": "invocation", "ok": True,
                                              "data": {"x": expected[1][0], "y": expected[1][1]}, "code": None})
                elif expected[0] == "invocation":
                    self.assertEqual((result["kind"], result["code"]), expected)
                    self.assertFalse(result["ok"])
                elif expected[0] in ("INAPPLICABLE", "UNKNOWN"):
                    self.assertEqual((result["kind"], result["status"]), ("applicability", expected[0]))
                    self.assertIn(expected[1], result["reasons"])
                elif expected[0] == "failure":
                    self.assertEqual((result["kind"], result["code"]), expected)
                else:
                    self.assertEqual((result["kind"], result["type"]), expected)
                self.assertEqual(actual["counts"], counts)
                self.assertEqual(actual["position"], position)
                self.assertEqual(actual["gateway_count"], 1)
                if name not in ("missing", "legacy_name", "bad_history"):
                    self.assertEqual(actual["provider_delta"], {"environment": 1, "frame": 1, "time": 1}
                                     if name != "provider_exception" else {"environment": 1, "frame": 1, "time": 0})
                else:
                    self.assertEqual(actual["provider_delta"], {"environment": 0, "frame": 0, "time": 0})
        # An additional same-process change proves providers are request-scoped;
        # the matrix above independently proves no result/grant crosses processes.
        changed = self.run_b(a_result, body=True, authorize=True, steps=[
            {"id": "alpha_1", "scenario": "same_process_first"},
            {"id": "alpha_1", "scenario": "same_process_frame_removed", "context": {"frame_asserted": False}},
        ])
        self.assertEqual(changed[0]["counts"], [1, 1, 1])
        self.assertEqual(changed[1]["counts"], [0, 0, 0])
        self.assertEqual(changed[1]["result"]["status"], "UNKNOWN")
        self.assertEqual(changed[1]["result"]["reasons"], ["FRAME_CONTEXT_MISSING"])
        self.assertEqual(changed[1]["position"], [10, 20])
        self.assertEqual(changed[0]["provider_delta"], {"environment": 1, "frame": 1, "time": 1})
        self.assertEqual(changed[1]["provider_delta"], {"environment": 1, "frame": 1, "time": 1})

        history = self.run_b(a_result, body=True, authorize=False, steps=[
            {"id": "parameter", "historical": True},
        ])[0]
        self.assertEqual(history["result"], {"kind": "historical",
                                             "record_id": ids["parameter"],
                                             "record_kind": "body_parameter_observation"})
        self.assertEqual(history["counts"], [0, 0, 0])
        self.assertEqual(history["position"], [0, 0])
        self.assertEqual({name: (self.state_b / name).read_bytes() for name in SELF_NAMES}, original_self)


if __name__ == "__main__":
    unittest.main()
