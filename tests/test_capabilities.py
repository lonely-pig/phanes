"""P0.1/P0.3 capability tests (Freeze section 14, test_capabilities).

Covers: contract importability, frozen data structures, safe defaults,
rejection of illegal values, the contracts module's dependency boundary,
the CapabilityRegistry authorization model (AT05-AT07, AT10), MockUAV
behavior, and explicit discovery (AT15).
"""

import ast
import contextlib
import io
import subprocess
import sys
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import phanes
from phanes import contracts
from phanes.capabilities import CapabilityRegistry, RegistryError
from phanes.contracts import (
    ADAPTER_API_VERSION,
    CAPABILITY_CONTRACT_VERSION,
    IDENTITY_SCHEMA_VERSION,
    KNOWN_CAPABILITIES,
    MEMORY_SCHEMA_VERSION,
    MOVE_TO,
    PACKAGE_VERSION,
    BodyAdapter,
    BodyDescriptor,
    CapabilitySpec,
    ContractError,
    Identity,
    InvocationResult,
    Position,
    is_valid_coordinate,
    validate_capability_args,
    validate_capability_result,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDependencyBoundary(unittest.TestCase):
    """contracts.py must depend only on the standard library."""

    def test_contracts_imports_only_stdlib(self):
        tree = ast.parse((REPO_ROOT / "phanes" / "contracts.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        non_stdlib = imported - set(sys.stdlib_module_names)
        self.assertEqual(non_stdlib, set(), f"non-stdlib imports in contracts.py: {non_stdlib}")
        self.assertNotIn("phanes_host", imported)
        self.assertNotIn("phanes", imported)

    def test_generic_runtime_does_not_import_host_package(self):
        """Importing the generic runtime alone must never import phanes_host."""
        code = (
            "import sys\n"
            "import phanes.contracts, phanes.identity, phanes.memory\n"
            "import phanes.capabilities, phanes.discovery\n"
            "assert 'phanes_host' not in sys.modules\n"
            "assert 'phanes_host.mock_uav' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class TestVersionConstants(unittest.TestCase):
    def test_p0_versions_are_fixed(self):
        self.assertEqual(IDENTITY_SCHEMA_VERSION, 1)
        self.assertEqual(MEMORY_SCHEMA_VERSION, 1)
        self.assertEqual(ADAPTER_API_VERSION, 1)
        self.assertEqual(CAPABILITY_CONTRACT_VERSION, 1)
        self.assertEqual(PACKAGE_VERSION, 1)
        self.assertEqual(phanes.__version__, "0.1")

    def test_error_codes(self):
        for code in (
            "NO_BODY",
            "CAPABILITY_UNAVAILABLE",
            "INVALID_ARGUMENT",
            "ADAPTER_ERROR",
            "INVALID_RESULT",
            "UNKNOWN_PLACE",
            "INVALID_COMMAND",
        ):
            self.assertEqual(getattr(contracts, code), code)


class TestDataStructures(unittest.TestCase):
    def test_position(self):
        pos = Position(x=10, y=20)
        self.assertEqual((pos.x, pos.y), (10, 20))
        with self.assertRaises(Exception):  # frozen
            pos.x = 99  # type: ignore[misc]

    def test_identity_defaults_to_schema_version_1(self):
        ident = Identity(agent_id="7d4d40b9-9f8a-40b1-80ec-cb0333bdb2b7", name="Phanes", created_at="2026-09-22T00:00:00Z")
        self.assertEqual(ident.schema_version, 1)

    def test_body_descriptor(self):
        desc = BodyDescriptor(body_id="mock-uav-B", body_type="mock_uav", adapter_api_version=1)
        self.assertEqual(desc.adapter_api_version, ADAPTER_API_VERSION)

    def test_capability_spec_defaults_to_contract_v1(self):
        spec = CapabilitySpec(name="move_to")
        self.assertEqual(spec.contract_version, 1)

    def test_invocation_result_shapes(self):
        ok = InvocationResult.success({"x": 10, "y": 20})
        self.assertTrue(ok.ok)
        self.assertEqual(ok.data, {"x": 10, "y": 20})
        self.assertIsNone(ok.error)

        err = InvocationResult.failure("CAPABILITY_UNAVAILABLE", "move_to is not registered")
        self.assertFalse(err.ok)
        self.assertIsNone(err.data)
        self.assertEqual(err.error.code, "CAPABILITY_UNAVAILABLE")


class TestCoordinates(unittest.TestCase):
    def test_accepts_finite_numbers(self):
        for value in (0, 1, -1, 10, -3, 2.5, -0.75, 1.25, -1.25):
            self.assertTrue(is_valid_coordinate(value), value)

    def test_rejects_bool_nan_inf_and_non_numbers(self):
        for value in (True, False, float("nan"), float("inf"), float("-inf"), "1", "10", None, (1, 2)):
            self.assertFalse(is_valid_coordinate(value), value)

    def test_accepts_arbitrary_finite_integers_without_float_conversion(self):
        for value in (10**400, -(10**400)):
            self.assertTrue(is_valid_coordinate(value))

    def test_rejects_other_numeric_types(self):
        for value in (Decimal("1"), Fraction(1, 2)):
            self.assertFalse(is_valid_coordinate(value))


class TestValidateCapabilityArgs(unittest.TestCase):
    def test_get_position_accepts_empty_args(self):
        validate_capability_args("get_position", {})

    def test_get_position_rejects_extra_fields(self):
        with self.assertRaises(ContractError):
            validate_capability_args("get_position", {"x": 1})

    def test_move_to_accepts_exact_keys(self):
        validate_capability_args("move_to", {"x": 10, "y": -2.5})

    def test_move_to_accepts_huge_finite_integer_coordinates(self):
        validate_capability_args(MOVE_TO, {"x": 10**400, "y": -(10**400)})

    def test_move_to_rejects_missing_key(self):
        with self.assertRaises(ContractError):
            validate_capability_args("move_to", {"x": 10})

    def test_move_to_rejects_extra_key(self):
        with self.assertRaises(ContractError):
            validate_capability_args("move_to", {"x": 10, "y": 20, "z": 30})

    def test_move_to_rejects_bool_and_non_finite(self):
        for bad in (True, False, float("nan"), float("inf"), float("-inf"), "10"):
            with self.assertRaises(ContractError, msg=repr(bad)):
                validate_capability_args("move_to", {"x": bad, "y": 20})

    def test_unknown_capability_rejected(self):
        with self.assertRaises(ContractError):
            validate_capability_args("land", {})

    def test_non_dict_args_rejected(self):
        with self.assertRaises(ContractError):
            validate_capability_args("move_to", [10, 20])


class TestValidateCapabilityResult(unittest.TestCase):
    def test_accepts_position_result(self):
        validate_capability_result("move_to", {"x": 10, "y": 20})
        validate_capability_result("get_position", {"x": 0, "y": 0})

    def test_accepts_huge_finite_integer_result(self):
        validate_capability_result(MOVE_TO, {"x": 10**400, "y": -(10**400)})

    def test_rejects_wrong_keys(self):
        for data in ({"x": 10}, {"x": 10, "y": 20, "z": 0}, {"ok": True}):
            with self.assertRaises(ContractError, msg=repr(data)):
                validate_capability_result("move_to", data)

    def test_rejects_invalid_values(self):
        for bad in (True, float("nan"), float("-inf"), "10", None):
            with self.assertRaises(ContractError, msg=repr(bad)):
                validate_capability_result("get_position", {"x": bad, "y": 0})

    def test_rejects_non_dict(self):
        with self.assertRaises(ContractError):
            validate_capability_result("move_to", (10, 20))


class FakeAdapter:
    """Minimal local test double implementing the BodyAdapter Protocol."""

    def __init__(self):
        self._descriptor = BodyDescriptor(body_id="fake-1", body_type="fake", adapter_api_version=1)
        self.calls = []

    @property
    def descriptor(self):
        return self._descriptor

    def capabilities(self):
        return (CapabilitySpec("get_position"), CapabilitySpec("move_to"))

    def invoke(self, name, args):
        self.calls.append((name, args))
        return {"x": 0, "y": 0}


class TestBodyAdapterProtocol(unittest.TestCase):
    def test_conforming_adapter_accepted(self):
        self.assertIsInstance(FakeAdapter(), BodyAdapter)

    def test_incomplete_adapter_rejected(self):
        class NoInvoke:
            @property
            def descriptor(self):
                return None

            def capabilities(self):
                return ()

        self.assertNotIsInstance(NoInvoke(), BodyAdapter)

    def test_known_capabilities_fixed(self):
        self.assertEqual(KNOWN_CAPABILITIES, frozenset({"get_position", "move_to"}))


class RaisingAdapter(FakeAdapter):
    def invoke(self, name, args):
        self.calls.append((name, args))
        raise RuntimeError("simulated adapter failure")


class BadResultAdapter(FakeAdapter):
    def invoke(self, name, args):
        self.calls.append((name, args))
        return {"x": "not-a-number", "y": 0}


class TestCapabilityRegistry(unittest.TestCase):
    def make_mock(self):
        from phanes_host.mock_uav import MockUAVAdapter

        return MockUAVAdapter(body_id="mock-uav-B")

    def bound_registry(self, allowed):
        registry = CapabilityRegistry()
        registry.bind(self.make_mock(), allowed)
        return registry

    # --- AT07: no body bound ---
    def test_empty_registry_has_no_body_and_denies_invoke(self):
        registry = CapabilityRegistry()
        self.assertIsNone(registry.current_body)
        self.assertEqual(registry.list(), ())
        result = registry.invoke("get_position", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "NO_BODY")

    # --- AT05: default zero authority ---
    def test_empty_allowed_registers_nothing(self):
        adapter = self.make_mock()
        registry = CapabilityRegistry()
        registry.bind(adapter, [])
        self.assertEqual(registry.list(), ())
        self.assertIsNotNone(registry.current_body)
        result = registry.invoke("move_to", {"x": 10, "y": 20})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "CAPABILITY_UNAVAILABLE")

    def test_adapter_supports_but_host_did_not_authorize(self):
        """Permission boundary: existence != authority."""
        adapter = self.make_mock()
        self.assertEqual(
            {spec.name for spec in adapter.capabilities()}, {"get_position", "move_to"}
        )
        registry = CapabilityRegistry()
        registry.bind(adapter, ["get_position"])
        self.assertEqual([spec.name for spec in registry.list()], ["get_position"])

        result = registry.invoke("move_to", {"x": 10, "y": 20})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "CAPABILITY_UNAVAILABLE")
        self.assertEqual(result.error.message, "move_to is not registered")
        # the unauthorized call must never reach the adapter
        self.assertEqual(adapter.invoke("get_position", {}), {"x": 0, "y": 0})

    # --- AT06: unregistered capability, zero adapter calls ---
    def test_unknown_capability_rejected_without_adapter_call(self):
        counting = FakeAdapter()
        registry = CapabilityRegistry()
        registry.bind(counting, ["get_position", "move_to"])

        result = registry.invoke("land", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "CAPABILITY_UNAVAILABLE")
        self.assertEqual(counting.calls, [])

        registry2 = CapabilityRegistry()
        registry2.bind(counting, ["get_position"])
        result = registry2.invoke("move_to", {"x": 1, "y": 2})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "CAPABILITY_UNAVAILABLE")
        self.assertEqual(counting.calls, [])

    # --- happy path ---
    def test_authorized_capability_invokes_and_returns_validated_result(self):
        adapter = self.make_mock()
        registry = CapabilityRegistry()
        registry.bind(adapter, ["get_position", "move_to"])
        with contextlib.redirect_stdout(io.StringIO()):
            result = registry.invoke("move_to", {"x": 10, "y": 20})
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"x": 10, "y": 20})
        self.assertIsNone(result.error)

    def test_authorized_mock_uav_accepts_huge_integer_once(self):
        adapter = self.make_mock()
        registry = CapabilityRegistry()
        registry.bind(adapter, [MOVE_TO])
        coordinates = {"x": 10**400, "y": -(10**400)}
        with patch.object(adapter, "invoke", wraps=adapter.invoke) as invoke:
            with contextlib.redirect_stdout(io.StringIO()):
                result = registry.invoke(MOVE_TO, coordinates)
        self.assertTrue(result.ok)
        self.assertEqual(result.data, coordinates)
        self.assertIsNone(result.error)
        invoke.assert_called_once_with(MOVE_TO, coordinates)

    # --- AT10 capability subcases: argument validation ---
    def test_invalid_arguments_rejected_before_adapter_call(self):
        counting = FakeAdapter()
        registry = CapabilityRegistry()
        registry.bind(counting, ["get_position", "move_to"])
        bad_calls = [
            ("move_to", {"x": 10}),                          # missing
            ("move_to", {"x": 10, "y": 20, "z": 30}),        # extra
            ("move_to", {"x": True, "y": 20}),               # bool
            ("move_to", {"x": float("nan"), "y": 20}),       # NaN
            ("move_to", {"x": 10, "y": float("inf")}),       # Infinity
            ("move_to", {"x": 10, "y": float("-inf")}),      # -Infinity
            ("move_to", {"x": "10", "y": 20}),               # wrong type
            ("get_position", {"x": 1}),                      # extra
        ]
        for name, args in bad_calls:
            result = registry.invoke(name, args)
            self.assertFalse(result.ok, (name, args))
            self.assertEqual(result.error.code, "INVALID_ARGUMENT", (name, args))
        self.assertEqual(counting.calls, [])

    # --- AT10: adapter failure is not success and never retried ---
    def test_adapter_exception_becomes_adapter_error_no_retry(self):
        adapter = RaisingAdapter()
        registry = CapabilityRegistry()
        registry.bind(adapter, ["move_to"])
        result = registry.invoke("move_to", {"x": 1, "y": 2})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "ADAPTER_ERROR")
        self.assertIn("simulated adapter failure", result.error.message)
        self.assertEqual(len(adapter.calls), 1)  # exactly one attempt

    def test_invalid_adapter_result_rejected(self):
        adapter = BadResultAdapter()
        registry = CapabilityRegistry()
        registry.bind(adapter, ["get_position"])
        result = registry.invoke("get_position", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "INVALID_RESULT")

    # --- P0.3.1 Fix 1: unknown offered capability rejected at bind ---
    def test_bind_rejects_unknown_offered_capability_no_partial_state(self):
        class LandAdapter(FakeAdapter):
            def capabilities(self):
                return (CapabilitySpec("get_position"), CapabilitySpec("land"))

        registry = CapabilityRegistry()
        with self.assertRaises(RegistryError):
            registry.bind(LandAdapter(), ["get_position", "land"])
        # failure must not leave a partially bound registry
        self.assertIsNone(registry.current_body)
        self.assertEqual(registry.list(), ())
        self.assertEqual(registry.invoke("get_position", {}).error.code, "NO_BODY")
        # and the registry recovers for a valid bind
        registry.bind(FakeAdapter(), ["get_position"])
        self.assertEqual([spec.name for spec in registry.list()], ["get_position"])

    def test_bind_rejects_unknown_offered_even_when_not_allowed(self):
        class LandAdapter(FakeAdapter):
            def capabilities(self):
                return (CapabilitySpec("get_position"), CapabilitySpec("land"))

        with self.assertRaises(RegistryError):
            CapabilityRegistry().bind(LandAdapter(), ["get_position"])

    def test_wildcard_authorization_rejected(self):
        with self.assertRaises(RegistryError):
            self.bound_registry(["*"])

    # --- P0.3.1 Fix 2: startup contract validation ---
    def test_bind_rejects_non_descriptor(self):
        class BadDesc(FakeAdapter):
            @property
            def descriptor(self):
                return {"body_id": "x", "body_type": "fake", "adapter_api_version": 1}

        with self.assertRaises(RegistryError):
            CapabilityRegistry().bind(BadDesc(), [])

    def test_bind_rejects_invalid_descriptor_fields(self):
        for bad_id, bad_type in (("", "fake"), ("   ", "fake"), (None, "fake"), ("x", ""), ("x", 42)):
            class BadFields(FakeAdapter):
                @property
                def descriptor(self):
                    return BodyDescriptor(
                        body_id=bad_id, body_type=bad_type, adapter_api_version=1
                    )

            with self.assertRaises(RegistryError, msg=(bad_id, bad_type)):
                CapabilityRegistry().bind(BadFields(), [])

    def test_bind_rejects_non_capability_spec_entries(self):
        class StringCaps(FakeAdapter):
            def capabilities(self):
                return ("move_to",)

        with self.assertRaises(RegistryError):
            CapabilityRegistry().bind(StringCaps(), [])

    def test_bind_rejects_empty_capability_name(self):
        class EmptyName(FakeAdapter):
            def capabilities(self):
                return (CapabilitySpec(""),)

        with self.assertRaises(RegistryError):
            CapabilityRegistry().bind(EmptyName(), [])

    def test_bind_wraps_adapter_internal_errors(self):
        class ExplodingDesc(FakeAdapter):
            @property
            def descriptor(self):
                raise AttributeError("boom")

        with self.assertRaises(RegistryError):
            CapabilityRegistry().bind(ExplodingDesc(), [])

        class ExplodingCaps(FakeAdapter):
            def capabilities(self):
                raise TypeError("boom")

        with self.assertRaises(RegistryError):
            CapabilityRegistry().bind(ExplodingCaps(), [])

    # --- bind failures (Freeze section 8 rule 2, AT15) ---
    def test_double_bind_rejected(self):
        registry = self.bound_registry([])
        with self.assertRaises(RegistryError):
            registry.bind(self.make_mock(), [])

    def test_bind_rejects_unknown_allowed_name(self):
        with self.assertRaises(RegistryError):
            self.bound_registry(["get_position", "land"])

    def test_bind_rejects_duplicate_allowed_names(self):
        with self.assertRaises(RegistryError):
            self.bound_registry(["move_to", "move_to"])

    def test_bind_rejects_duplicate_offered_names(self):
        class DupAdapter(FakeAdapter):
            def capabilities(self):
                return (CapabilitySpec("move_to"), CapabilitySpec("move_to"))

        with self.assertRaises(RegistryError):
            CapabilityRegistry().bind(DupAdapter(), [])

    def test_bind_rejects_incompatible_versions(self):
        class BadApiAdapter(FakeAdapter):
            @property
            def descriptor(self):
                return BodyDescriptor(body_id="x", body_type="fake", adapter_api_version=2)

        with self.assertRaises(RegistryError):
            CapabilityRegistry().bind(BadApiAdapter(), [])

        class BadContractAdapter(FakeAdapter):
            def capabilities(self):
                return (CapabilitySpec("move_to", contract_version=2),)

        with self.assertRaises(RegistryError):
            CapabilityRegistry().bind(BadContractAdapter(), ["move_to"])


class TestMockUAVAdapter(unittest.TestCase):
    def setUp(self):
        from phanes_host.mock_uav import MockUAVAdapter

        self.adapter = MockUAVAdapter(body_id="mock-uav-B")

    def test_implements_body_adapter_protocol(self):
        self.assertIsInstance(self.adapter, BodyAdapter)

    def test_descriptor(self):
        desc = self.adapter.descriptor
        self.assertEqual(desc.body_id, "mock-uav-B")
        self.assertEqual(desc.body_type, "mock_uav")
        self.assertEqual(desc.adapter_api_version, ADAPTER_API_VERSION)

    def test_capabilities_match_fixed_contract(self):
        specs = self.adapter.capabilities()
        self.assertEqual({s.name for s in specs}, {"get_position", "move_to"})
        self.assertTrue(all(s.contract_version == 1 for s in specs))

    def test_initial_position_is_origin(self):
        self.assertEqual(self.adapter.invoke("get_position", {}), {"x": 0, "y": 0})

    def test_move_to_updates_position(self):
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.adapter.invoke("move_to", {"x": 10, "y": 20})
        self.assertEqual(result, {"x": 10, "y": 20})
        self.assertEqual(self.adapter.invoke("get_position", {}), {"x": 10, "y": 20})

    def test_move_to_logs_exactly_once_with_integer_display(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.adapter.invoke("move_to", {"x": 10, "y": 20})
        self.assertEqual(buf.getvalue(), "[MockUAV] moving to (10, 20)\n")

    def test_move_to_decimal_display(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.adapter.invoke("move_to", {"x": -3.5, "y": 0.25})
        self.assertEqual(buf.getvalue(), "[MockUAV] moving to (-3.5, 0.25)\n")

    def test_direct_invoke_validates_contract(self):
        with self.assertRaises(ContractError):
            self.adapter.invoke("move_to", {"x": True, "y": 20})
        with self.assertRaises(ContractError):
            self.adapter.invoke("land", {})


class TestDiscovery(unittest.TestCase):
    def test_explicit_mock_uav_config_discovers_body(self):
        from phanes.discovery import discover_body
        from phanes_host.mock_uav import MockUAVAdapter

        adapter = discover_body(
            {
                "schema_version": 1,
                "body": {"adapter": "mock_uav", "body_id": "mock-uav-B"},
                "allowed_capabilities": ["get_position", "move_to"],
            }
        )
        self.assertIsInstance(adapter, MockUAVAdapter)
        self.assertEqual(adapter.descriptor.body_id, "mock-uav-B")

    def test_null_body_means_no_body(self):
        from phanes.discovery import discover_body

        self.assertIsNone(
            discover_body({"schema_version": 1, "body": None, "allowed_capabilities": []})
        )
        self.assertIsNone(discover_body({"schema_version": 1}))  # defaults: no body, no grants

    def test_null_body_with_grants_is_config_error(self):
        from phanes.discovery import DiscoveryError, discover_body

        with self.assertRaises(DiscoveryError):
            discover_body({"schema_version": 1, "body": None, "allowed_capabilities": ["move_to"]})

    def test_invalid_configs_fail_loudly(self):
        from phanes.discovery import DiscoveryError, discover_body

        bad_configs = [
            {"schema_version": 2, "body": None},                                       # bad version
            {"schema_version": True, "body": None},                                    # bool version
            {"body": None},                                                            # missing version
            {"schema_version": 1, "body": None, "extra": 1},                           # unknown field
            {"schema_version": 1, "body": {"adapter": "ros2", "body_id": "x"}},        # unknown adapter
            {"schema_version": 1, "body": {"adapter": "mock_uav"}},                    # missing body_id
            {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": ""}},     # empty body_id
            {"schema_version": 1, "body": "mock_uav"},                                 # body not an object
            {"schema_version": 1, "allowed_capabilities": "move_to"},                  # not a list
            {"schema_version": 1, "allowed_capabilities": ["move_to", 42]},            # non-string entry
            ["not", "an", "object"],                                                   # config not an object
        ]
        for config in bad_configs:
            with self.assertRaises(DiscoveryError, msg=repr(config)):
                discover_body(config)

    def test_load_host_config(self):
        from phanes.discovery import DiscoveryError, load_host_config

        config = load_host_config(REPO_ROOT / "examples" / "host.mock-uav.json")
        self.assertEqual(config["body"]["adapter"], "mock_uav")

        with self.assertRaises(DiscoveryError):
            load_host_config(REPO_ROOT / "examples" / "does-not-exist.json")

    def test_discovery_is_explicit_no_scanning_no_host_import(self):
        """AT15: null/unknown configs neither scan nor import the host package."""
        code = (
            "import sys\n"
            "from phanes.discovery import DiscoveryError, discover_body\n"
            "assert discover_body({'schema_version': 1, 'body': None}) is None\n"
            "try:\n"
            "    discover_body({'schema_version': 1, 'body': {'adapter': 'ros2', 'body_id': 'x'}})\n"
            "except DiscoveryError:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit('unknown adapter accepted')\n"
            "assert 'phanes_host' not in sys.modules, sys.modules.keys()\n"
            "assert 'phanes_host.mock_uav' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_example_config_grants_do_not_leak_without_authorization(self):
        """Discovery never grants by itself: authority comes only from the
        explicit allowed list handed to the registry."""
        from phanes.discovery import load_host_config

        config = load_host_config(REPO_ROOT / "examples" / "host.mock-uav.json")
        self.assertEqual(config["allowed_capabilities"], ["get_position", "move_to"])
        # Dropping the grant from the same config leaves the registry empty.
        registry = CapabilityRegistry()
        from phanes.discovery import discover_body

        registry.bind(discover_body(config), [])
        self.assertEqual(registry.list(), ())


if __name__ == "__main__":
    unittest.main()
