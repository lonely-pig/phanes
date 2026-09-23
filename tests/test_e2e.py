"""P0.4 tests: Core command flow, CLI loop, init transaction (Freeze §14).

Covers the single-environment memory->move loop, AT07 (no body), AT08
(swap body with a local test double, Core unchanged), AT10 command-side
subcases, and the Core dependency boundary (no discovery / phanes_host,
no registry bypass, no authority grants).
"""

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from phanes import __main__ as cli
from phanes.capabilities import CapabilityRegistry
from phanes.contracts import (
    BodyDescriptor,
    CapabilitySpec,
    InvocationResult,
)
from phanes.core import PhanesCore
from phanes.identity import IDENTITY_FILENAME, init_identity
from phanes.memory import MEMORY_FILENAME, MemoryStore, MemoryStoreError

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeBodyAdapter:
    """Local test double implementing the same contract (AT08)."""

    def __init__(self, body_id="fake-body"):
        self._descriptor = BodyDescriptor(body_id=body_id, body_type="fake", adapter_api_version=1)
        self.x, self.y = 0, 0
        self.calls = []

    @property
    def descriptor(self):
        return self._descriptor

    def capabilities(self):
        return (CapabilitySpec("get_position"), CapabilitySpec("move_to"))

    def invoke(self, name, args):
        self.calls.append((name, args))
        if name == "move_to":
            self.x, self.y = args["x"], args["y"]
        return {"x": self.x, "y": self.y}


class SpyRegistry(CapabilityRegistry):
    """Records registry interactions to prove Core's call path."""

    def __init__(self):
        super().__init__()
        self.events = []
        self.bind_calls = 0

    def bind(self, adapter, allowed_capabilities):
        self.bind_calls += 1
        super().bind(adapter, allowed_capabilities)

    def list(self):
        self.events.append(("list",))
        return super().list()

    def invoke(self, name, args):
        self.events.append(("invoke", name, dict(args)))
        return super().invoke(name, args)


class CoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "state"
        self.identity = init_identity(self.state)
        self.memory = MemoryStore.create(self.state, self.identity.agent_id)
        self.adapter = FakeBodyAdapter()
        self.registry = SpyRegistry()

    def bind(self, allowed):
        self.registry.bind(self.adapter, allowed)

    def core(self):
        return PhanesCore(self.identity, self.memory, self.registry)


class TestCoreMoveFlow(CoreTestCase):
    def test_move_alpha_success_exact_call_chain(self):
        self.memory.remember_place("Alpha", 10, 20)
        self.bind(["get_position", "move_to"])
        result = self.core().handle_command("去 Alpha。")

        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"x": 10, "y": 20})
        # lookup -> registry.list -> registry.invoke, exactly one adapter call
        self.assertEqual(
            self.registry.events, [("list",), ("invoke", "move_to", {"x": 10, "y": 20})]
        )
        self.assertEqual(self.adapter.calls, [("move_to", {"x": 10, "y": 20})])
        self.assertEqual((self.adapter.x, self.adapter.y), (10, 20))

    def test_move_unknown_place_fails_before_registry(self):
        self.bind(["move_to"])
        result = self.core().handle_command("去 Nowhere。")
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "UNKNOWN_PLACE")
        self.assertEqual(self.registry.events, [])  # no list, no invoke
        self.assertEqual(self.adapter.calls, [])

    def test_move_unauthorized_capability(self):
        self.memory.remember_place("Alpha", 10, 20)
        self.bind(["get_position"])  # move_to not granted
        result = self.core().handle_command("去 Alpha。")
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "CAPABILITY_UNAVAILABLE")
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual((self.adapter.x, self.adapter.y), (0, 0))

    def test_move_without_body_returns_no_body(self):
        self.memory.remember_place("Alpha", 10, 20)
        result = self.core().handle_command("去 Alpha。")  # registry never bound
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "NO_BODY")
        self.assertEqual(self.registry.events, [])

    def test_swap_body_core_unchanged(self):
        """AT08: the same Core works with a different contract-conforming
        body; Core source is not modified and does not import phanes_host."""
        self.memory.remember_place("Alpha", 10, 20)
        self.bind(["move_to"])
        result = self.core().handle_command("去 Alpha。")
        self.assertTrue(result.ok)
        self.assertEqual((self.adapter.x, self.adapter.y), (10, 20))
        self.assertEqual(self.core().current_body.body_type, "fake")


class TestCoreMemoryCommands(CoreTestCase):
    def test_query_known_and_unknown_place(self):
        self.memory.remember_place("Alpha", 10, 20)
        core = self.core()

        result = core.handle_command("查询 Alpha。")
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"action": "query", "place": "Alpha", "x": 10, "y": 20})

        result = core.handle_command("查询 Nowhere。")
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "UNKNOWN_PLACE")

    def test_remember_persists_both_punctuation_forms(self):
        core = self.core()
        r1 = core.handle_command("记住 Alpha 是坐标 (10, 20)。")
        r2 = core.handle_command("记住Beta是坐标（-3.5，0.25）")
        self.assertTrue(r1.ok)
        self.assertTrue(r2.ok)
        self.assertEqual(r1.data, {"action": "remember", "place": "Alpha", "x": 10, "y": 20})

        reloaded = MemoryStore.load(self.state, self.identity.agent_id)
        self.assertEqual((reloaded.lookup_place("Alpha").x, reloaded.lookup_place("Alpha").y), (10, 20))
        pos = reloaded.lookup_place("Beta")
        self.assertEqual((pos.x, pos.y), (-3.5, 0.25))

    def test_remember_rejects_non_finite_coordinates(self):
        core = self.core()
        for bad in ("nan", "inf", "-inf"):
            result = core.handle_command(f"记住 Gamma 是坐标 ({bad}, 1)。")
            self.assertFalse(result.ok, bad)
            self.assertEqual(result.error.code, "INVALID_COMMAND", bad)
        self.assertIsNone(self.memory.lookup_place("Gamma"))

    def test_invalid_commands(self):
        self.bind(["move_to"])
        core = self.core()
        for line in (
            "你好",
            "去",
            "查询",
            "记住 Alpha",
            "去 Al pha",
            "查询 A。b",
            "记住 Alpha 是坐标 (10)。",
            "记住 Alpha 是坐标 (10, 20, 30)。",
            "记住 Alpha 是坐标 (abc, 20)。",
            "move Alpha",
            "where",
        ):
            result = core.handle_command(line)
            self.assertFalse(result.ok, line)
            self.assertEqual(result.error.code, "INVALID_COMMAND", line)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.registry.events, [])


class TestCoreAuthorityBoundary(CoreTestCase):
    def test_core_never_binds_or_grants(self):
        core = self.core()
        core.handle_command("记住 Alpha 是坐标 (1, 2)。")
        core.handle_command("查询 Alpha。")
        core.handle_command("去 Alpha。")
        self.assertEqual(self.registry.bind_calls, 0)
        self.assertEqual(self.registry.list(), ())
        self.assertIsNone(core.current_body)

    def test_core_views_reflect_injected_registry(self):
        self.bind(["get_position"])
        core = self.core()
        self.assertEqual(core.current_body.body_id, "fake-body")
        self.assertEqual([spec.name for spec in core.capabilities], ["get_position"])


class TestCoreDependencyBoundary(unittest.TestCase):
    def test_core_module_imports_only_allowed_dependencies(self):
        tree = ast.parse((REPO_ROOT / "phanes" / "core.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module)
        for module in imported:
            top = module.split(".")[0]
            self.assertIn(top, set(sys.stdlib_module_names) | {"phanes"}, module)
        self.assertNotIn("phanes.discovery", imported)
        self.assertFalse(any(m.startswith("phanes_host") for m in imported), imported)

    def test_importing_core_loads_neither_discovery_nor_host(self):
        code = (
            "import sys\n"
            "import phanes.core\n"
            "assert 'phanes.discovery' not in sys.modules\n"
            "assert 'phanes_host' not in sys.modules\n"
            "assert 'phanes_host.mock_uav' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class TestInitTransaction(unittest.TestCase):
    def test_init_success_creates_complete_state(self):
        with TemporaryDirectory() as tmp:
            final = Path(tmp) / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = cli.main(["init", "--state", str(final)])
            self.assertEqual(rc, 0)
            self.assertTrue((final / IDENTITY_FILENAME).is_file())
            self.assertTrue((final / MEMORY_FILENAME).is_file())

    def test_init_refuses_existing_path(self):
        with TemporaryDirectory() as tmp:
            final = Path(tmp) / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["init", "--state", str(final)]), 0)
            before = (final / IDENTITY_FILENAME).read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                rc = cli.main(["init", "--state", str(final)])
            self.assertEqual(rc, 1)
            self.assertEqual((final / IDENTITY_FILENAME).read_bytes(), before)

    def test_failed_init_leaves_no_partial_state_and_retries(self):
        with TemporaryDirectory() as tmp:
            final = Path(tmp) / "state"
            with unittest.mock.patch.object(
                MemoryStore, "create", side_effect=MemoryStoreError("simulated failure")
            ):
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = cli.main(["init", "--state", str(final)])
            self.assertEqual(rc, 1)
            self.assertFalse(final.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])  # temp dir cleaned

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["init", "--state", str(final)]), 0)
            self.assertTrue((final / IDENTITY_FILENAME).is_file())
            self.assertTrue((final / MEMORY_FILENAME).is_file())


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = self.root / "state"

    def run_cli(self, args, stdin_text=""):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, "-m", "phanes", *args],
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=REPO_ROOT,
            env=env,
        )

    def init_state(self):
        result = self.run_cli(["init", "--state", str(self.state)])
        self.assertEqual(result.returncode, 0, result.stderr)
        identity = json.loads((self.state / IDENTITY_FILENAME).read_text(encoding="utf-8"))
        return identity

    def write_host_config(self, allowed, name="host.json"):
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "body": {"adapter": "mock_uav", "body_id": "mock-uav-B"},
                    "allowed_capabilities": allowed,
                }
            ),
            encoding="utf-8",
        )
        return path


class TestCliLoop(CliTestCase):
    def test_full_single_environment_loop(self):
        identity = self.init_state()
        host = self.write_host_config(["get_position", "move_to"])
        script = "记住 Alpha 是坐标 (10, 20)。\n查询 Alpha。\n去 Alpha。\n退出\n"
        result = self.run_cli(
            ["run", "--state", str(self.state), "--host-config", str(host)], script
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn(f"agent_id: {identity['agent_id']}", out)
        self.assertIn("body: mock-uav-B (mock_uav)", out)
        self.assertIn("capabilities: get_position, move_to", out)
        self.assertIn("已保存 Alpha = (10, 20)", out)
        self.assertIn("Alpha = (10, 20)", out)
        self.assertEqual(out.count("[MockUAV] moving to (10, 20)"), 1)
        self.assertIn("已到达 (10, 20)", out)

    def test_run_without_host_config_has_no_body(self):
        self.init_state()
        script = "记住 Alpha 是坐标 (10, 20)。\n查询 Alpha。\n去 Alpha。\n退出\n"
        result = self.run_cli(["run", "--state", str(self.state)], script)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("body: none", out)
        self.assertIn("capabilities: (none)", out)
        self.assertIn("已保存 Alpha = (10, 20)", out)  # memory works without a body
        self.assertIn("NO_BODY", out)
        self.assertNotIn("[MockUAV]", out)

    def test_run_with_partial_authorization_denies_move(self):
        self.init_state()
        host = self.write_host_config(["get_position"])
        script = "记住 Alpha 是坐标 (10, 20)。\n去 Alpha。\n退出\n"
        result = self.run_cli(
            ["run", "--state", str(self.state), "--host-config", str(host)], script
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CAPABILITY_UNAVAILABLE: move_to is not registered", result.stdout)
        self.assertNotIn("[MockUAV]", result.stdout)

    def test_run_reports_unknown_place_and_invalid_command(self):
        self.init_state()
        host = self.write_host_config(["move_to"])
        script = "去 Nowhere。\n随便说说。\n退出\n"
        result = self.run_cli(
            ["run", "--state", str(self.state), "--host-config", str(host)], script
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UNKNOWN_PLACE: unknown place: Nowhere", result.stdout)
        self.assertIn("INVALID_COMMAND", result.stdout)
        self.assertNotIn("[MockUAV]", result.stdout)

    def test_eof_exits_normally(self):
        self.init_state()
        result = self.run_cli(["run", "--state", str(self.state)], "查询 Alpha。\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_with_missing_state_fails(self):
        result = self.run_cli(["run", "--state", str(self.root / "no-such-state")], "退出\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)

    def test_run_with_corrupt_identity_fails_without_regeneration(self):
        self.init_state()
        path = self.state / IDENTITY_FILENAME
        path.write_text("{ corrupt", encoding="utf-8")
        result = self.run_cli(["run", "--state", str(self.state)], "退出\n")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(path.read_text(encoding="utf-8"), "{ corrupt")

    def test_run_with_unknown_adapter_config_fails(self):
        self.init_state()
        bad = self.root / "bad-host.json"
        bad.write_text(
            json.dumps({"schema_version": 1, "body": {"adapter": "ros2", "body_id": "x"}}),
            encoding="utf-8",
        )
        result = self.run_cli(
            ["run", "--state", str(self.state), "--host-config", str(bad)], "退出\n"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown adapter", result.stderr)


if __name__ == "__main__":
    unittest.main()
