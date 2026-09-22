"""P0.1 contract tests (Freeze section 14, test_capabilities contract cases).

Covers: contract importability, frozen data structures, safe defaults,
rejection of illegal values, and the contracts module's dependency boundary.
Registry/MockUAV behavior tests are added in P0.3.
"""

import ast
import sys
import unittest
from pathlib import Path

import phanes
from phanes import contracts
from phanes.contracts import (
    ADAPTER_API_VERSION,
    CAPABILITY_CONTRACT_VERSION,
    IDENTITY_SCHEMA_VERSION,
    KNOWN_CAPABILITIES,
    MEMORY_SCHEMA_VERSION,
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
        self.assertNotIn("phanes_host.mock_uav", sys.modules)


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
        for value in (0, 10, -3, 2.5, -0.75):
            self.assertTrue(is_valid_coordinate(value), value)

    def test_rejects_bool_nan_inf_and_non_numbers(self):
        for value in (True, False, float("nan"), float("inf"), float("-inf"), "10", None, (1, 2)):
            self.assertFalse(is_valid_coordinate(value), value)


class TestValidateCapabilityArgs(unittest.TestCase):
    def test_get_position_accepts_empty_args(self):
        validate_capability_args("get_position", {})

    def test_get_position_rejects_extra_fields(self):
        with self.assertRaises(ContractError):
            validate_capability_args("get_position", {"x": 1})

    def test_move_to_accepts_exact_keys(self):
        validate_capability_args("move_to", {"x": 10, "y": -2.5})

    def test_move_to_rejects_missing_key(self):
        with self.assertRaises(ContractError):
            validate_capability_args("move_to", {"x": 10})

    def test_move_to_rejects_extra_key(self):
        with self.assertRaises(ContractError):
            validate_capability_args("move_to", {"x": 10, "y": 20, "z": 30})

    def test_move_to_rejects_bool_and_non_finite(self):
        for bad in (True, float("nan"), float("inf"), "10"):
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


if __name__ == "__main__":
    unittest.main()
