"""P0.6 isolated A/B migration acceptance (Freeze sections 13-14).

Every A-side and B-side step runs as a separate OS process with its own
directory. B imports with its own pre-installed tool copy, runs the
*package* runtime (cwd = B/transfer/runtime) with PYTHONPATH pointing only
at B's host directory, and is exercised while A's directory is moved away
(AT13). Runtime body state never migrates (AT14); authority never migrates
(AT09 behavior). No in-process objects are shared between A and B.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent

# B-side acceptance tool: composes Core exactly like the CLI bootstrap and
# additionally prints the evidence AT04/AT13/AT14 require (runtime path,
# sys.path, position before/after). Runs with cwd = package runtime.
B_VERIFY_SCRIPT = """\
import json
import sys
from pathlib import Path

import phanes
from phanes.capabilities import CapabilityRegistry
from phanes.core import PhanesCore
from phanes.discovery import discover_body, load_host_config
from phanes.identity import load_identity
from phanes.memory import MemoryStore

state = Path(sys.argv[1])
config = load_host_config(sys.argv[2])
identity = load_identity(state)
memory = MemoryStore.load(state, identity.agent_id)
registry = CapabilityRegistry()
adapter = discover_body(config)
registry.bind(adapter, config["allowed_capabilities"])
core = PhanesCore(identity, memory, registry)

print("pid:", __import__("os").getpid())
print("phanes_runtime:", Path(phanes.__file__).resolve())
print("sys_path:", json.dumps(sys.path))
print("agent_id:", identity.agent_id)
print("alpha:", memory.lookup_place("Alpha"))
print("position_before:", registry.invoke("get_position", {}).data)
result = core.handle_command("去 Alpha。")
print("move_ok:", result.ok)
print("position_after:", registry.invoke("get_position", {}).data)
"""


B_POSITION_SCRIPT = """\
import sys
from pathlib import Path

from phanes.capabilities import CapabilityRegistry
from phanes.discovery import discover_body, load_host_config

config = load_host_config(sys.argv[2])
registry = CapabilityRegistry()
adapter = discover_body(config)
registry.bind(adapter, config["allowed_capabilities"])
print("position:", registry.invoke("get_position", {}).data)
"""


class ProcResult:
    def __init__(self, pid, returncode, stdout, stderr):
        self.pid = pid
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class IsolatedABAcceptance(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.env_a = self.root / "env_a"
        self.env_b = self.root / "env_b"
        self.state_a = self.env_a / "state"
        self.state_b = self.env_b / "state"
        self.package_a = self.env_a / "transfer"
        self.package_b = self.env_b / "transfer"
        self.runtime_b = self.package_b / "runtime"
        self.tool_b = self.env_b / "tool"  # B's pre-installed trusted migration tool
        self.host_b = self.env_b / "host"  # B's pre-installed host package
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(REPO_ROOT / "phanes", self.tool_b / "phanes", ignore=ignore)
        shutil.copytree(REPO_ROOT / "phanes_host", self.host_b / "phanes_host", ignore=ignore)
        self._write_host_config(self.env_a / "host.json", "mock-uav-A", ["get_position", "move_to"])
        self._write_host_config(self.env_b / "host.json", "mock-uav-B", ["get_position", "move_to"])
        self.pids = {}

    @staticmethod
    def _write_host_config(path, body_id, allowed):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "body": {"adapter": "mock_uav", "body_id": body_id},
                    "allowed_capabilities": allowed,
                }
            ),
            encoding="utf-8",
        )

    def run_py(self, label, args, cwd, stdin="", pythonpath=None):
        """Run a step as a fresh OS process. PYTHONPATH is explicitly reset:
        only the given pythonpath (if any) is visible — never the repo."""
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        if pythonpath is not None:
            env["PYTHONPATH"] = str(pythonpath)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [sys.executable, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=cwd,
            env=env,
        )
        out, err = proc.communicate(input=stdin)
        self.pids[label] = proc.pid
        return ProcResult(proc.pid, proc.returncode, out, err)

    def prepare_a_and_package(self):
        """Environment A: init, remember+query+move via CLI, export, manual copy."""
        r = self.run_py("A.init", ["-m", "phanes", "init", "--state", str(self.state_a)], cwd=REPO_ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)

        r = self.run_py(
            "A.run",
            ["-m", "phanes", "run", "--state", str(self.state_a), "--host-config", str(self.env_a / "host.json")],
            cwd=REPO_ROOT,
            stdin="记住 Alpha 是坐标 (10, 20)。\n查询 Alpha。\n去 Alpha。\n退出\n",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("已保存 Alpha = (10, 20)", r.stdout)
        self.assertIn("Alpha = (10, 20)", r.stdout)
        self.assertIn("body: mock-uav-A (mock_uav)", r.stdout)
        # A's own body moved to (10,20) — runtime state that must NOT migrate
        self.assertEqual(r.stdout.count("[MockUAV] moving to (10, 20)"), 1)

        r = self.run_py(
            "A.export",
            ["-m", "phanes", "export", "--state", str(self.state_a), "--out", str(self.package_a)],
            cwd=REPO_ROOT,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

        # the manual, offline copy A -> B (the test stands in for the human)
        shutil.copytree(self.package_a, self.package_b)
        agent_id_a = json.loads((self.state_a / "identity.json").read_text(encoding="utf-8"))["agent_id"]
        return agent_id_a

    def import_on_b(self, agent_id_a):
        """B imports using B's own pre-installed tool copy, not the repo."""
        r = self.run_py(
            "B.import",
            ["-m", "phanes", "import", "--package", str(self.package_b), "--state", str(self.state_b)],
            cwd=self.tool_b,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"agent_id: {agent_id_a}", r.stdout)

    def test_full_isolated_migration_loop(self):
        """AT03/AT04/AT09/AT13/AT14: the complete P0 acceptance loop."""
        agent_id_a = self.prepare_a_and_package()
        self.import_on_b(agent_id_a)

        # AT13: A becomes unreachable before B runs
        shutil.move(self.env_a, self.root / "env_a_gone")

        # --- B without any host config: authority did not migrate (AT09) ---
        r = self.run_py(
            "B.run.noauth",
            ["-m", "phanes", "run", "--state", str(self.state_b)],
            cwd=self.runtime_b,
            pythonpath=self.host_b,
            stdin="查询 Alpha。\n去 Alpha。\n退出\n",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"agent_id: {agent_id_a}", r.stdout)  # AT03 identity continuity
        self.assertIn("Alpha = (10, 20)", r.stdout)  # AT03 memory continuity
        self.assertIn("body: none", r.stdout)
        self.assertIn("capabilities: (none)", r.stdout)
        self.assertIn("NO_BODY", r.stdout)  # zero authority after import
        self.assertNotIn("[MockUAV]", r.stdout)

        # --- B with its own new body + new authorization: the move (AT04) ---
        r = self.run_py(
            "B.run",
            ["-m", "phanes", "run", "--state", str(self.state_b), "--host-config", str(self.env_b / "host.json")],
            cwd=self.runtime_b,
            pythonpath=self.host_b,
            stdin="去 Alpha。\n退出\n",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"agent_id: {agent_id_a}", r.stdout)
        self.assertIn("body: mock-uav-B (mock_uav)", r.stdout)  # body replacement
        self.assertIn("capabilities: get_position, move_to", r.stdout)
        self.assertEqual(r.stdout.count("[MockUAV] moving to (10, 20)"), 1)

        # --- position + runtime-isolation evidence (AT04/AT13/AT14) ---
        r = self.run_py(
            "B.verify",
            ["-c", B_VERIFY_SCRIPT, str(self.state_b), str(self.env_b / "host.json")],
            cwd=self.runtime_b,
            pythonpath=self.host_b,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"agent_id: {agent_id_a}", r.stdout)
        self.assertIn("alpha: Position(x=10, y=20)", r.stdout)
        self.assertIn("position_before: {'x': 0, 'y': 0}", r.stdout)  # AT14: A's position did not migrate
        self.assertIn("move_ok: True", r.stdout)
        self.assertIn("position_after: {'x': 10, 'y': 20}", r.stdout)  # AT04
        self.assertEqual(r.stdout.count("[MockUAV] moving to (10, 20)"), 1)

        runtime_line = next(l for l in r.stdout.splitlines() if l.startswith("phanes_runtime:"))
        runtime_path = Path(runtime_line.split(":", 1)[1].strip())
        self.assertTrue(str(runtime_path).startswith(str(self.runtime_b.resolve())), runtime_path)
        sys_path = json.loads(next(l for l in r.stdout.splitlines() if l.startswith("sys_path:"))[9:])
        for entry in sys_path:
            resolved = str(Path(entry or ".").resolve()) if entry else str(self.runtime_b.resolve())
            self.assertFalse(
                resolved.startswith(str(REPO_ROOT)),
                f"B saw the dev repo on sys.path: {entry}",
            )

        # process isolation: every step ran in its own OS process, none of
        # them is this test process
        labels = ["A.init", "A.run", "A.export", "B.import", "B.run.noauth", "B.run", "B.verify"]
        pids = [self.pids[label] for label in labels]
        self.assertNotIn(os.getpid(), pids)
        self.assertGreater(len(set(pids)), 1, pids)

    def test_b_with_body_but_zero_grants_cannot_move(self):
        """AT05 at the isolated level: B's MockUAV present, no grants."""
        agent_id_a = self.prepare_a_and_package()
        self.import_on_b(agent_id_a)
        shutil.move(self.env_a, self.root / "env_a_gone")

        self._write_host_config(self.env_b / "host-noauth.json", "mock-uav-B", [])
        r = self.run_py(
            "B.run.emptyauth",
            [
                "-m", "phanes", "run",
                "--state", str(self.state_b),
                "--host-config", str(self.env_b / "host-noauth.json"),
            ],
            cwd=self.runtime_b,
            pythonpath=self.host_b,
            stdin="去 Alpha。\n退出\n",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("body: mock-uav-B (mock_uav)", r.stdout)
        self.assertIn("capabilities: (none)", r.stdout)
        self.assertIn("CAPABILITY_UNAVAILABLE: move_to is not registered", r.stdout)
        self.assertNotIn("[MockUAV]", r.stdout)

        # position is still the fresh origin: nothing moved in the denied
        # process (checked with a get_position-only authorization)
        r = self.run_py(
            "B.verify.nomove",
            ["-c", B_POSITION_SCRIPT, str(self.state_b), str(self.env_b / "host.json")],
            cwd=self.runtime_b,
            pythonpath=self.host_b,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("position: {'x': 0, 'y': 0}", r.stdout)
        self.assertNotIn("[MockUAV]", r.stdout)


if __name__ == "__main__":
    unittest.main()
