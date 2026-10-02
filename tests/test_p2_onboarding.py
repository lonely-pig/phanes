"""P2.1 declaration/API boundary and inert bootstrap import."""

import dataclasses
import json
import subprocess
import sys
import unittest
from pathlib import Path

from phanes.contracts import BodyDescriptor, CapabilitySpec
from phanes_host import p2_bootstrap
from phanes_host.p2_bootstrap import HostSessionDescriptor, OnboardingError

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestHostSessionDescriptor(unittest.TestCase):
    def descriptor(self, **changes):
        values = dict(
            host_session_id="opaque-test-session",
            body=BodyDescriptor("body-b", "mock_uav", 1),
            offered_capabilities=(CapabilitySpec("get_position"), CapabilitySpec("move_to")),
            context_source_kinds=("body_instance_id", "environment_id", "frame_id", "evaluation_time"),
        )
        values.update(changes)
        return HostSessionDescriptor(**values)

    def test_descriptor_is_read_only_and_contains_no_authority(self):
        descriptor = self.descriptor()
        expected = {"host_session_id", "body", "offered_capabilities", "context_source_kinds"}
        self.assertEqual({field.name for field in dataclasses.fields(descriptor)}, expected)
        self.assertFalse(hasattr(descriptor, "__dict__"))
        for name in expected:
            with self.subTest(name=name), self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(descriptor, name, None)
        for forbidden in (
            "authority", "allowed_capabilities", "environment_id", "frame_id", "evaluation_time",
            "position", "battery", "mode", "armed", "adapter", "registry", "provider",
            "module", "path", "url", "pending_action", "execution_state",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(hasattr(descriptor, forbidden))
                with self.assertRaises(TypeError):
                    self.descriptor(**{forbidden: object()})
        for method in ("save", "load", "to_dict", "from_dict", "to_json", "from_json", "serialize"):
            self.assertFalse(hasattr(descriptor, method))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            descriptor.body.body_id = "replacement"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            descriptor.offered_capabilities[0].name = "replacement"

    def test_descriptor_reuses_existing_body_and_capability_types(self):
        body = BodyDescriptor("body-b", "mock_uav", 1)
        offered = (CapabilitySpec("move_to"),)
        descriptor = self.descriptor(body=body, offered_capabilities=offered)
        self.assertIs(descriptor.body, body)
        self.assertIs(descriptor.offered_capabilities, offered)
        self.assertIs(type(descriptor.body), BodyDescriptor)
        self.assertIs(type(descriptor.offered_capabilities[0]), CapabilitySpec)

    def test_descriptor_rejects_mutable_containers_and_live_values(self):
        invalid = (
            {"host_session_id": object()},
            {"body": {"body_id": "body-b"}},
            {"body": BodyDescriptor(object(), "mock_uav", 1)},
            {"body": BodyDescriptor("body-b", lambda: None, 1)},
            {"body": BodyDescriptor("body-b", "mock_uav", object())},
            {"offered_capabilities": [CapabilitySpec("move_to")]},
            {"offered_capabilities": (object(),)},
            {"offered_capabilities": (CapabilitySpec(lambda: None),)},
            {"offered_capabilities": (CapabilitySpec("move_to", object()),)},
            {"context_source_kinds": ["frame_id"]},
            {"context_source_kinds": (lambda: None,)},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(TypeError):
                self.descriptor(**values)

    def test_context_source_kinds_are_only_fixed_p1_names(self):
        for kinds in (("battery",), ("host_session_id",), ("frame_id", "frame_id")):
            with self.subTest(kinds=kinds), self.assertRaises(ValueError):
                self.descriptor(context_source_kinds=kinds)
        self.assertEqual(self.descriptor(context_source_kinds=()).context_source_kinds, ())
        # A declaration is not a provider, a readiness assertion, or a binding.
        self.assertEqual(self.descriptor(context_source_kinds=("frame_id",)).context_source_kinds, ("frame_id",))

    def test_session_id_is_supplied_opaque_data_not_generated_or_normalized(self):
        value = " operator-chosen opaque value "
        self.assertEqual(self.descriptor(host_session_id=value).host_session_id, value)
        for value in ("", "   "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.descriptor(host_session_id=value)
        self.assertTrue(all(field.default_factory is dataclasses.MISSING for field in dataclasses.fields(HostSessionDescriptor)))

    def test_public_surface_has_no_partial_onboarding_or_binding_api(self):
        self.assertEqual(p2_bootstrap.__all__, ("HostSessionDescriptor", "OnboardingError"))
        self.assertTrue(issubclass(OnboardingError, Exception))
        self.assertFalse(hasattr(OnboardingError("diagnostic"), "code"))
        for name in ("onboard_host_session", "HostSessionBinding", "grant", "reset", "rebind", "compose_p1_runtime"):
            self.assertFalse(hasattr(p2_bootstrap, name))
        # Declaration-only shape validation is not a duplicate Registry validator.
        descriptor = self.descriptor(offered_capabilities=(CapabilitySpec("unknown", 2),))
        self.assertEqual(descriptor.offered_capabilities[0].contract_version, 2)


class TestBootstrapImport(unittest.TestCase):
    def run_fresh_import(self):
        code = r'''
import json, sys, uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import phanes.discovery as discovery
import phanes.capabilities as capabilities
import phanes.p1_runtime as runtime
from phanes.experience_gateway import TargetContextSources
from phanes_host.mock_uav import MockUAVAdapter
calls = dict.fromkeys(("adapter_construction", "discovery", "registry_construction", "bind",
                      "compose", "snapshot", "action", "session_id"), 0)
def forbidden(name):
    def fail(*args, **kwargs):
        calls[name] += 1
        raise AssertionError(name + " happened on import")
    return fail
with (
    patch.object(MockUAVAdapter, "__init__", forbidden("adapter_construction")),
    patch.object(MockUAVAdapter, "invoke", forbidden("action")),
    patch.object(discovery, "discover_body", forbidden("discovery")),
    patch.object(capabilities.CapabilityRegistry, "__init__", forbidden("registry_construction")),
    patch.object(capabilities.CapabilityRegistry, "bind", forbidden("bind")),
    patch.object(runtime, "compose_p1_runtime", forbidden("compose")),
    patch.object(TargetContextSources, "snapshot", forbidden("snapshot")),
    patch.object(uuid, "uuid4", forbidden("session_id")),
):
    import phanes_host.p2_bootstrap as bootstrap
assert "phanes_host._p0_discovery" not in sys.modules
assert not hasattr(bootstrap, "onboard_host_session")
assert not hasattr(bootstrap, "host_session_id")
print(json.dumps(calls))
'''
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(REPO_ROOT)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_bootstrap_module_import_performs_no_onboarding(self):
        self.assertEqual(set(self.run_fresh_import().values()), {0})

    def test_bootstrap_import_creates_no_session_id(self):
        self.assertEqual(self.run_fresh_import()["session_id"], 0)


if __name__ == "__main__":
    unittest.main()
