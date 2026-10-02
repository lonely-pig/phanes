"""P2 declaration/import invariants and P2.2 one-shot private assembly."""

import dataclasses
import importlib.util
import inspect
import json
import shutil
import subprocess
import sys
import threading
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from phanes.applicability import ApplicabilityResult, ApplicabilityStatus, evaluate_experience_use
from phanes.capabilities import CapabilityRegistry, RegistryError
from phanes.contracts import BodyAdapter, BodyDescriptor, CapabilitySpec, CAPABILITY_UNAVAILABLE, MOVE_TO
from phanes.experience_core import _ExperienceOperationalCore
from phanes.experience_contracts import FRAME_CONTEXT_MISSING, BODY_MISMATCH
from phanes.experience_store import ExperienceStore
from phanes.identity import init_identity
from phanes.memory import MemoryStore
from phanes.migration_v2 import SELF_FILES_V2, export_package_v2
from phanes.p1_runtime import compose_p1_runtime
from phanes_host.mock_uav import MockUAVAdapter
from phanes_host import p2_bootstrap
from phanes_host.p2_bootstrap import HostSessionDescriptor, OnboardingError
from test_experience_semantics import record

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
        self.assertEqual(p2_bootstrap.__all__, ("HostSessionDescriptor", "OnboardingError", "onboard_host_session"))
        self.assertTrue(callable(p2_bootstrap.onboard_host_session))
        self.assertTrue(issubclass(OnboardingError, Exception))
        self.assertFalse(hasattr(OnboardingError("diagnostic"), "code"))
        for name in ("HostSessionBinding", "grant", "reset", "rebind", "compose_p1_runtime"):
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
assert callable(bootstrap.onboard_host_session)
assert bootstrap._published is False
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


class TestOneShotOnboarding(unittest.TestCase):
    """Fresh test-only module namespaces isolate permanent publication guards.

    This is not a supported reset/reload API. Actual module/process behavior
    is also checked in a fresh subprocess below.
    """

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
        self.before_self = self.self_bytes()
        self.config = {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": "body-a"}, "allowed_capabilities": []}
        self.values = {"environment": "env-a", "frame": "frame-a", "time": None}
        self.providers = {
            "get_environment_id": Mock(side_effect=lambda: self.values["environment"]),
            "get_asserted_frame_id": Mock(side_effect=lambda: self.values["frame"]),
            "get_evaluation_time": Mock(side_effect=lambda: self.values["time"]),
        }
        # Install the exact trusted Discovery bytes under its fixed Host name.
        source = self.root / "_p0_discovery.py"
        shutil.copyfile(REPO_ROOT / "phanes" / "discovery.py", source)
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)

        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module

        self.discovery = load("phanes_host._p0_discovery", source)
        import phanes_host
        host_dependency = patch.object(phanes_host, "_p0_discovery", self.discovery, create=True)
        host_dependency.start()
        self.addCleanup(host_dependency.stop)
        self.bootstrap = load("_p2_unit_bootstrap", REPO_ROOT / "phanes_host" / "p2_bootstrap.py")
        self.adapters = []
        self.registries = []
        self.counts = dict.fromkeys(("construction", "descriptor", "capabilities", "action", "bind"), 0)
        self.offered = (CapabilitySpec("get_position"), CapabilitySpec(MOVE_TO))
        self.body = BodyDescriptor("body-a", "mock_uav", 1)
        self.unstable = False
        owner = self

        class CountedAdapter(MockUAVAdapter):
            def __init__(self, body_id):
                owner.counts["construction"] += 1
                super().__init__(body_id)
                owner.adapters.append(self)

            @property
            def descriptor(self):
                owner.counts["descriptor"] += 1
                if owner.unstable and owner.counts["descriptor"] > 1:
                    return BodyDescriptor("changed-body", "mock_uav", 1)
                return owner.body

            def capabilities(self):
                owner.counts["capabilities"] += 1
                if owner.unstable and owner.counts["capabilities"] > 1:
                    return (CapabilitySpec("unknown", 99),)
                return owner.offered

            def invoke(self, name, args):
                owner.counts["action"] += 1
                return super().invoke(name, args)

        self.counted_adapter = CountedAdapter
        factories = patch.dict(self.discovery._ADAPTER_FACTORIES, {"mock_uav": CountedAdapter})
        factories.start()
        self.addCleanup(factories.stop)
        self.discover = self.start_patch(patch.object(self.discovery, "discover_body", wraps=self.discovery.discover_body))
        original_bind = CapabilityRegistry.bind

        def bind(registry, adapter, allowed):
            self.counts["bind"] += 1
            self.registries.append(registry)
            return original_bind(registry, adapter, allowed)

        self.start_patch(patch.object(CapabilityRegistry, "bind", bind))
        self.compose = self.start_patch(patch("phanes.p1_runtime.compose_p1_runtime", wraps=compose_p1_runtime))
        self.uuid = self.start_patch(patch("uuid.uuid4", wraps=uuid.uuid4))

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def self_bytes(self):
        return {name: (self.state / name).read_bytes() for name in SELF_FILES_V2}

    def onboard(self, **changes):
        args = dict(state_dir=self.state, body_config=self.config, **self.providers)
        args.update(changes)
        return self.bootstrap.onboard_host_session(**args)

    def assert_prepared_without_action(self):
        self.assertEqual(self.counts, dict(construction=1, descriptor=1, capabilities=1, action=0, bind=1))
        for provider in self.providers.values():
            provider.assert_not_called()

    def assert_failure(self, **changes):
        with self.assertRaises(self.bootstrap.OnboardingError) as caught:
            self.onboard(**changes)
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertFalse(self.bootstrap._published)
        self.assertEqual(self.counts["action"], 0)
        self.uuid.assert_not_called()
        for provider in self.providers.values():
            provider.assert_not_called()
        self.assertEqual(vars(caught.exception), {})
        self.assertEqual(self.self_bytes(), self.before_self)
        return caught.exception

    def execute_counted(self, runtime):
        registry = self.registries[-1]
        with patch.object(registry, "list", wraps=registry.list) as listed, patch.object(registry, "invoke", wraps=registry.invoke) as invoked:
            result = runtime.navigate_experience(self.experience_id)
        return result, (listed.call_count, invoked.call_count, self.counts["action"])

    def test_zero_authority_is_a_complete_published_session(self):
        descriptor, runtime = self.onboard()
        self.assert_prepared_without_action()
        self.assertTrue(self.bootstrap._published)
        self.assertEqual(descriptor.offered_capabilities, self.offered)
        self.assertIs(descriptor.body, self.body)
        self.assertEqual(self.registries[0].list(), ())
        self.assertEqual(self.uuid.call_count, 1)
        result, counts = self.execute_counted(runtime)
        self.assertEqual((result.error.code, counts), (CAPABILITY_UNAVAILABLE, (1, 0, 0)))

    def test_offered_is_not_authority_and_real_p1_action_invokes_once(self):
        descriptor, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        self.assert_prepared_without_action()
        self.assertEqual(descriptor.offered_capabilities, self.offered)
        self.assertEqual(self.registries[0].list(), (CapabilitySpec(MOVE_TO),))
        self.assertEqual(descriptor.context_source_kinds, ("body_instance_id", "environment_id", "frame_id", "evaluation_time"))
        self.assertIsInstance(self.registries[0]._adapter, BodyAdapter)
        with patch("phanes.experience_gateway.evaluate_experience_use", wraps=evaluate_experience_use) as evaluate:
            result, counts = self.execute_counted(runtime)
        self.assertEqual(counts, (1, 1, 1))
        self.assertEqual(result.data, {"x": 10, "y": 20})
        context = evaluate.call_args.args[3]
        self.assertEqual(context.body_instance_id, "body-a")
        self.assertFalse(hasattr(context, "host_session_id"))
        for provider in self.providers.values():
            self.assertEqual(provider.call_count, 1)
        self.assertEqual(self.counts["descriptor"], 1)
        self.assertEqual(self.counts["capabilities"], 1)
        self.assertIs(type(runtime), _ExperienceOperationalCore)

    def test_nonempty_config_authority_is_rejected_not_merged(self):
        self.config["allowed_capabilities"] = [MOVE_TO]
        self.assert_failure(allowed_capabilities=(MOVE_TO,))
        self.discover.assert_not_called()
        self.assertEqual(self.counts["construction"], 0)

    def test_noncallable_and_missing_providers_fail_before_preparation(self):
        for name in self.providers:
            for value in (None, "env-a", 7):
                with self.subTest(name=name, value=value):
                    self.assert_failure(**{name: value})
        self.discover.assert_not_called()
        self.assertEqual(self.counts["construction"], 0)
        args = dict(state_dir=self.state, body_config=self.config, **self.providers)
        del args["get_evaluation_time"]
        with self.assertRaises(self.bootstrap.OnboardingError) as caught:
            self.bootstrap.onboard_host_session(**args)
        self.assertIsInstance(caught.exception.__cause__, TypeError)
        self.assertFalse(self.bootstrap._published)
        self.onboard()  # missing/noncallable input did not consume publication

    def test_callable_none_is_accepted_then_p1_reports_unknown(self):
        self.values.update(environment=None, frame=None, time=None)
        _, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        self.assert_prepared_without_action()
        result, counts = self.execute_counted(runtime)
        self.assertIsInstance(result, ApplicabilityResult)
        self.assertIs(result.status, ApplicabilityStatus.UNKNOWN)
        self.assertIn(FRAME_CONTEXT_MISSING, result.reason_codes)
        self.assertEqual(counts, (0, 0, 0))
        self.assertEqual([p.call_count for p in self.providers.values()], [1, 1, 1])

    def test_provider_exception_is_only_a_request_error(self):
        failure = RuntimeError("current frame provider failed")
        self.providers["get_asserted_frame_id"].side_effect = failure
        _, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        self.assert_prepared_without_action()
        with patch.object(self.registries[0], "list") as listed, patch.object(self.registries[0], "invoke") as invoked:
            with self.assertRaises(RuntimeError) as caught:
                runtime.navigate_experience(self.experience_id)
        self.assertIs(caught.exception, failure)
        listed.assert_not_called()
        invoked.assert_not_called()
        self.assertEqual(self.counts["action"], 0)
        self.assertTrue(self.bootstrap._published)

    def test_wrong_capability_version_fails_in_existing_registry(self):
        self.offered = (CapabilitySpec(MOVE_TO, 2),)
        error = self.assert_failure()
        self.assertIsInstance(error.__cause__, RegistryError)
        self.assertEqual(self.counts["bind"], 1)
        self.compose.assert_not_called()

    def test_unknown_offered_fails_in_existing_registry(self):
        self.offered = (CapabilitySpec("unknown"),)
        self.assertIsInstance(self.assert_failure().__cause__, RegistryError)
        self.compose.assert_not_called()

    def test_duplicate_offered_fails_in_existing_registry(self):
        self.offered = (CapabilitySpec(MOVE_TO), CapabilitySpec(MOVE_TO))
        self.assertIsInstance(self.assert_failure().__cause__, RegistryError)

    def test_invalid_body_declaration_fails_in_existing_registry(self):
        self.body = BodyDescriptor("body-a", "mock_uav", 2)
        self.assertIsInstance(self.assert_failure().__cause__, RegistryError)

    def test_unknown_authority_fails_without_publication(self):
        self.assertIsInstance(self.assert_failure(allowed_capabilities=("unknown",)).__cause__, RegistryError)

    def test_duplicate_authority_fails_without_publication(self):
        self.assertIsInstance(self.assert_failure(allowed_capabilities=(MOVE_TO, MOVE_TO)).__cause__, RegistryError)

    def test_invalid_authority_shape_does_not_publish(self):
        for allowed in (None, MOVE_TO, (7,)):
            with self.subTest(allowed=allowed):
                self.assert_failure(allowed_capabilities=allowed)

    def test_compose_failure_does_not_consume_publication_and_retry_succeeds(self):
        self.assert_failure(state_dir=self.root / "missing-self")
        self.assertEqual(self.counts["bind"], 1)
        self.assertEqual(self.compose.call_count, 1)
        descriptor, runtime = self.onboard()
        self.assertTrue(self.bootstrap._published)
        self.assertEqual(self.uuid.call_count, 1)
        self.assertEqual(descriptor.body, self.body)
        self.assertEqual(self.counts["bind"], 2)  # distinct private Registries
        self.assertIsNot(self.registries[0], self.registries[1])
        self.assertTrue(callable(runtime.navigate_experience))

    def test_invalid_self_fails_before_uuid_then_corrected_retry(self):
        experiences = self.state / "experiences.json"
        original = experiences.read_bytes()
        experiences.write_text("{}", encoding="utf-8")
        with self.assertRaises(self.bootstrap.OnboardingError) as caught:
            self.onboard()
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertFalse(self.bootstrap._published)
        self.uuid.assert_not_called()
        self.assertEqual(self.counts["action"], 0)
        experiences.write_bytes(original)
        self.onboard()
        self.assertTrue(self.bootstrap._published)

    def test_unstable_declarations_are_captured_exactly_once(self):
        self.unstable = True
        descriptor, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        self.assert_prepared_without_action()
        self.assertIs(descriptor.body, self.body)
        self.assertEqual(descriptor.offered_capabilities, self.offered)
        self.assertEqual(self.registries[0].current_body, descriptor.body)
        result, counts = self.execute_counted(runtime)
        self.assertTrue(result.ok)
        self.assertEqual(counts, (1, 1, 1))
        self.assertEqual((self.counts["descriptor"], self.counts["capabilities"]), (1, 1))

    def test_second_call_fails_before_all_preparation_old_runtime_still_works(self):
        descriptor, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        before = dict(self.counts)
        self.discover.reset_mock()
        self.compose.reset_mock()
        self.uuid.reset_mock()
        with patch.object(self.bootstrap, "_load_host_discovery", side_effect=AssertionError("loaded twice")) as loaded:
            with self.assertRaisesRegex(self.bootstrap.OnboardingError, "already published"):
                self.onboard(get_environment_id=None, allowed_capabilities=None)
        loaded.assert_not_called()
        self.discover.assert_not_called()
        self.compose.assert_not_called()
        self.uuid.assert_not_called()
        self.assertEqual(self.counts, before)
        for provider in self.providers.values():
            provider.assert_not_called()
        result, counts = self.execute_counted(runtime)
        self.assertTrue(result.ok)
        self.assertEqual(counts, (1, 1, 1))
        self.assertEqual(descriptor.body.body_id, "body-a")

    def test_uuid_failure_does_not_publish_then_retry_succeeds(self):
        failure = RuntimeError("UUID source unavailable")
        self.uuid.side_effect = failure
        with self.assertRaises(self.bootstrap.OnboardingError) as caught:
            self.onboard()
        self.assertIs(caught.exception.__cause__, failure)
        self.assertFalse(self.bootstrap._published)
        self.assert_prepared_without_action()
        self.assertEqual(self.uuid.call_count, 1)
        self.uuid.side_effect = None
        self.onboard()
        self.assertEqual(self.uuid.call_count, 2)
        self.assertTrue(self.bootstrap._published)

    def test_descriptor_failure_does_not_publish_then_retry_succeeds(self):
        with patch.object(self.bootstrap, "HostSessionDescriptor", side_effect=ValueError("declaration rejected")):
            with self.assertRaises(self.bootstrap.OnboardingError):
                self.onboard()
        self.assertFalse(self.bootstrap._published)
        self.assertEqual(self.counts["action"], 0)
        self.onboard()
        self.assertTrue(self.bootstrap._published)

    def test_no_body_returns_none_without_session_or_compose_then_retry(self):
        no_body = {"schema_version": 1, "body": None, "allowed_capabilities": []}
        self.assertIsNone(self.onboard(body_config=no_body))
        self.assertFalse(self.bootstrap._published)
        self.assertEqual(set(self.counts.values()), {0})
        self.uuid.assert_not_called()
        self.compose.assert_not_called()
        for provider in self.providers.values():
            provider.assert_not_called()
        self.assert_failure(body_config=no_body, allowed_capabilities=(MOVE_TO,))
        self.onboard()
        self.assertTrue(self.bootstrap._published)

    def test_bad_config_and_missing_discovery_do_not_publish(self):
        for config in ({"schema_version": 2}, {"schema_version": 1, "body": {"adapter": "plugin-url", "body_id": "body-a"}}, None):
            with self.subTest(config=config):
                self.assert_failure(body_config=config)
        with patch.object(self.bootstrap, "_load_host_discovery", side_effect=ImportError("missing fixed dependency")):
            self.assertIsInstance(self.assert_failure().__cause__, ImportError)
        self.assertEqual(self.counts["construction"], 0)
        self.onboard()

    def test_capture_failures_never_bind_compose_or_publish(self):
        for attr in ("descriptor", "capabilities"):
            with self.subTest(attr=attr):
                if attr == "descriptor":
                    replacement = property(lambda adapter: (_ for _ in ()).throw(RuntimeError("unreadable descriptor")))
                else:
                    replacement = Mock(side_effect=RuntimeError("unreadable capabilities"))
                with patch.object(self.counted_adapter, attr, replacement):
                    self.assert_failure()
        self.assertEqual(self.counts["bind"], 0)
        self.compose.assert_not_called()
        self.onboard()

    def test_adapter_construction_failure_is_retryable(self):
        failure = RuntimeError("trusted Adapter construction failed")
        with patch.dict(self.discovery._ADAPTER_FACTORIES, {"mock_uav": Mock(side_effect=failure)}):
            self.assertIs(self.assert_failure().__cause__, failure)
        self.assertEqual(self.counts["bind"], 0)
        self.compose.assert_not_called()
        self.onboard()
        self.assert_prepared_without_action()

    def test_overlapping_preparation_cannot_publish_two_sessions(self):
        entered = threading.Event()
        release = threading.Event()
        results = []
        errors = []
        original = self.discovery.discover_body

        def pause(config):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timed out awaiting release")
            return original(config)

        def first():
            try:
                results.append(self.onboard())
            except Exception as exc:
                errors.append(exc)

        with patch.object(self.discovery, "discover_body", side_effect=pause):
            worker = threading.Thread(target=first)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaisesRegex(self.bootstrap.OnboardingError, "in progress"):
                    self.onboard()
                self.assertEqual(set(self.counts.values()), {0})
                self.uuid.assert_not_called()
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assert_prepared_without_action()
        self.assertEqual(self.uuid.call_count, 1)
        self.assertTrue(self.bootstrap._published)

    def test_authority_is_passive_data_bound_once_and_no_grant_api_exists(self):
        # Sol P2.2 review B1: the public authority input is passive data. A
        # list is rejected before Discovery, and a tuple cannot be mutated, so
        # no caller code can run while authority is read; the allowed set is
        # bound exactly once and never re-read from a live source.
        allowed = (MOVE_TO,)
        error = self.assert_failure(allowed_capabilities=[MOVE_TO])
        self.assertIsInstance(error.__cause__, TypeError)
        self.assertEqual(self.counts["construction"], 0)
        self.discover.assert_not_called()
        _, runtime = self.onboard(allowed_capabilities=allowed)
        self.assertEqual(allowed, (MOVE_TO,))
        self.assertEqual(self.registries[0].list(), (CapabilitySpec(MOVE_TO),))
        self.assertTrue(runtime.navigate_experience(self.experience_id).ok)
        for owner in (self.bootstrap, runtime, self.registries[0]):
            for name in ("grant", "reset", "rebind", "HostSessionBinding"):
                self.assertFalse(hasattr(owner, name))

    def test_session_is_ephemeral_self_and_package_remain_byte_identical(self):
        before_package = export_package_v2(self.state, self.root / "before-package")
        descriptor, _ = self.onboard()
        after_package = export_package_v2(self.state, self.root / "after-package")

        def bytes_by_path(root):
            return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

        self.assertEqual(self.self_bytes(), self.before_self)
        self.assertEqual(bytes_by_path(before_package), bytes_by_path(after_package))
        session_bytes = descriptor.host_session_id.encode()
        self.assertFalse(any(session_bytes in data for data in bytes_by_path(after_package).values()))

    def test_current_context_is_not_cached_and_authority_does_not_override_applicability(self):
        _, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        first, counts = self.execute_counted(runtime)
        self.assertTrue(first.ok)
        self.values["frame"] = None
        second, counts = self.execute_counted(runtime)
        self.assertIs(second.status, ApplicabilityStatus.UNKNOWN)
        self.assertIn(FRAME_CONTEXT_MISSING, second.reason_codes)
        self.assertEqual(counts, (0, 0, 1))  # only the preceding action
        self.assertEqual([p.call_count for p in self.providers.values()], [2, 2, 2])

    def test_body_context_comes_from_registry_not_config_or_session_id(self):
        self.body = BodyDescriptor("body-b", "mock_uav", 1)
        descriptor, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        result, counts = self.execute_counted(runtime)
        self.assertEqual(descriptor.body.body_id, "body-b")
        self.assertIs(result.status, ApplicabilityStatus.INAPPLICABLE)
        self.assertIn(BODY_MISMATCH, result.reason_codes)
        self.assertEqual(counts, (0, 0, 0))

    def test_signature_accepts_no_live_binding_or_custom_loading_inputs(self):
        params = inspect.signature(self.bootstrap.onboard_host_session).parameters
        self.assertEqual(tuple(params), ("state_dir", "body_config", "allowed_capabilities", "get_environment_id", "get_asserted_frame_id", "get_evaluation_time"))
        self.assertEqual(params["allowed_capabilities"].default, ())
        for name in ("adapter", "registry", "module_path", "url", "factory", "host_session_id"):
            with self.subTest(name=name), self.assertRaises(TypeError):
                self.onboard(**{name: object()})
        self.assertFalse(self.bootstrap._published)

    def test_reentrant_preparation_is_rejected_without_consuming_outer_attempt(self):
        original = self.discovery.discover_body

        def recursive(config):
            with self.assertRaisesRegex(self.bootstrap.OnboardingError, "in progress"):
                self.onboard()
            return original(config)

        with patch.object(self.discovery, "discover_body", side_effect=recursive):
            self.onboard()
        self.assert_prepared_without_action()
        self.assertEqual(self.uuid.call_count, 1)

    def test_real_module_in_fresh_process_enforces_second_call_guard(self):
        code = r'''
import importlib.util, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import phanes_host
spec = importlib.util.spec_from_file_location("phanes_host._p0_discovery", sys.argv[3])
discovery = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = discovery
spec.loader.exec_module(discovery)
phanes_host._p0_discovery = discovery
from phanes_host import p2_bootstrap as bootstrap
from phanes.capabilities import CapabilityRegistry
from phanes_host.mock_uav import MockUAVAdapter
providers = dict(get_environment_id=lambda: "env-a", get_asserted_frame_id=lambda: "frame-a", get_evaluation_time=lambda: None)
config = {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": "body-a"}, "allowed_capabilities": []}
descriptor, runtime = bootstrap.onboard_host_session(sys.argv[2], config, allowed_capabilities=("move_to",), **providers)
def forbidden(*args, **kwargs):
    raise AssertionError("second call reached preparation")
with patch.object(bootstrap, "_load_host_discovery", forbidden), patch.object(CapabilityRegistry, "__init__", forbidden), patch.object(MockUAVAdapter, "__init__", forbidden), patch("uuid.uuid4", forbidden), patch("phanes.p1_runtime.compose_p1_runtime", forbidden):
    try:
        bootstrap.onboard_host_session(sys.argv[2], config, **providers)
    except bootstrap.OnboardingError as exc:
        assert "already published" in str(exc)
    else:
        raise AssertionError("second publication succeeded")
assert runtime.navigate_experience(sys.argv[4]).ok
print("REAL_PROCESS_PUBLICATION_PASS")
'''
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(REPO_ROOT), str(self.state), str(self.root / "_p0_discovery.py"), self.experience_id], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("REAL_PROCESS_PUBLICATION_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
