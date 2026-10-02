"""P2.5 failure injection and boundary hardening.

Tries to break P2 through malformed inputs, unstable/raising Adapter
declarations, Registry and compose failures, UUID/descriptor construction
failures, second onboarding, reentrancy/concurrency, the no-body edge,
dynamic-loading and repository-fallback attacks, persistence leaks, and a
public API audit. No functionality is added; every discovered defect would
be a fix inside the existing P2 files.
"""

import itertools
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from phanes.capabilities import CapabilityRegistry, RegistryError
from phanes.contracts import BodyDescriptor, CapabilitySpec, MOVE_TO, GET_POSITION
from phanes.experience_contracts import INVALID_EXPERIENCE
from phanes.experience_store import ExperienceStore
from phanes.identity import IdentityError, init_identity
from phanes.memory import MemoryStore, MemoryStoreError
from phanes.migration_v2 import SELF_FILES_V2, export_package_v2, import_package_v2
from phanes.p1_runtime import compose_p1_runtime
from phanes_host.mock_uav import MockUAVAdapter
from test_experience_semantics import record

REPO_ROOT = Path(__file__).resolve().parent.parent


class HardeningBase(unittest.TestCase):
    """Fresh module namespaces isolate the permanent publication guard."""

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
        source = self.root / "_p0_discovery.py"
        shutil.copyfile(REPO_ROOT / "phanes" / "discovery.py", source)
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)

        import importlib.util

        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module

        self.discovery = load("phanes_host._p0_discovery", source)
        # The installed Host dependency is a distinct module copy: its
        # DiscoveryError is a different class object from phanes.discovery's.
        self.discovery_error = self.discovery.DiscoveryError
        import phanes_host

        host_dependency = patch.object(phanes_host, "_p0_discovery", self.discovery, create=True)
        host_dependency.start()
        self.addCleanup(host_dependency.stop)
        self.bootstrap = load(
            f"_p2_hardening_bootstrap_{next(self._module_counter)}",
            REPO_ROOT / "phanes_host" / "p2_bootstrap.py",
        )
        self.adapters = []
        self.constructed_body_ids = []
        self.registries = []
        self.counts = dict.fromkeys(
            ("construction", "descriptor", "capabilities", "action", "bind", "registry_init"), 0)
        self.offered = (CapabilitySpec(GET_POSITION), CapabilitySpec(MOVE_TO))
        self.body = BodyDescriptor("body-a", "mock_uav", 1)
        self.unstable = False
        owner = self

        class CountedAdapter(MockUAVAdapter):
            def __init__(self, body_id):
                owner.counts["construction"] += 1
                owner.constructed_body_ids.append(body_id)
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
        original_registry_init = CapabilityRegistry.__init__

        def counting_registry_init(registry):
            self.counts["registry_init"] += 1
            original_registry_init(registry)

        self.start_patch(patch.object(CapabilityRegistry, "__init__", counting_registry_init))
        original_bind = CapabilityRegistry.bind

        def bind(registry, adapter, allowed):
            self.counts["bind"] += 1
            self.registries.append(registry)
            return original_bind(registry, adapter, allowed)

        self.start_patch(patch.object(CapabilityRegistry, "bind", bind))
        self.compose = self.start_patch(patch("phanes.p1_runtime.compose_p1_runtime", wraps=compose_p1_runtime))
        self.uuid = self.start_patch(patch("uuid.uuid4", wraps=uuid.uuid4))

    _module_counter = itertools.count()

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

    def assert_rejected(self, cause_type=None, check_self_bytes=True, **changes):
        with self.assertRaises(self.bootstrap.OnboardingError) as caught:
            self.onboard(**changes)
        self.assertIsNotNone(caught.exception.__cause__)
        if cause_type is not None:
            self.assertIsInstance(caught.exception.__cause__, cause_type)
        self.assertFalse(self.bootstrap._published)
        self.assertEqual(self.counts["action"], 0)
        self.uuid.assert_not_called()
        for provider in self.providers.values():
            provider.assert_not_called()
        self.assertEqual(vars(caught.exception), {})
        if check_self_bytes:
            self.assertEqual(self.self_bytes(), self.before_self)
        return caught.exception


class TestP251MalformedInputs(HardeningBase):
    def test_body_config_rejections_leave_no_trace(self):
        body = {"adapter": "mock_uav", "body_id": "body-a"}
        invalid_configs = (
            None, [], "config", 42, {},
            {"schema_version": 2},
            {"schema_version": "1"},
            {"schema_version": 1, "body": {"adapter": "mock_uav"}},
            {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": ""}},
            {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": 7}},
            {"schema_version": 1, "body": [], "allowed_capabilities": []},
            {"schema_version": 1, "body": body, "allowed_capabilities": [], "extra": 1},
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                self.assert_rejected(cause_type=self.discovery_error, body_config=config)
        self.assertEqual(self.counts["construction"], 0)
        # P2's own rules: ambiguous config-sourced authority is rejected before
        # Discovery, regardless of its type.
        for authority in ([MOVE_TO], "move_to", (MOVE_TO,)):
            with self.subTest(authority=authority):
                self.assert_rejected(cause_type=ValueError, body_config={
                    "schema_version": 1, "body": body, "allowed_capabilities": authority})
        self.assertEqual(self.counts["construction"], 0)
        self.onboard()
        self.assertTrue(self.bootstrap._published)

    def test_allowed_capabilities_content_rejections(self):
        invalid = (
            None, MOVE_TO, b"move_to", 7, (7,), (None,), (object(),), (MOVE_TO, None),
            ("unknown",), ("",), (MOVE_TO, MOVE_TO), (MOVE_TO, "unknown"), [MOVE_TO, MOVE_TO],
        )
        for allowed in invalid:
            with self.subTest(allowed=allowed):
                self.assert_rejected(allowed_capabilities=allowed)
        # The Adapter may already be constructed when Registry rejects the
        # authority content; the invariant is that nothing was ever invoked.
        self.assertEqual(self.counts["action"], 0)
        self.onboard(allowed_capabilities=(MOVE_TO,))
        self.assertTrue(self.bootstrap._published)

    def test_provider_input_rejections_before_any_host_work(self):
        for name in self.providers:
            for value in (None, 42, "env-a", ()):
                with self.subTest(name=name, value=value):
                    self.assert_rejected(cause_type=TypeError, **{name: value})
        args = dict(state_dir=self.state, body_config=self.config, **self.providers)
        del args["get_evaluation_time"]
        with self.assertRaises(self.bootstrap.OnboardingError) as caught:
            self.bootstrap.onboard_host_session(**args)
        self.assertIsInstance(caught.exception.__cause__, TypeError)
        self.discover.assert_not_called()
        self.assertEqual(set(self.counts.values()), {0})
        self.onboard()
        self.assertTrue(self.bootstrap._published)


class TestP252UnstableDeclarations(HardeningBase):
    def test_declaration_snapshot_taken_once_and_shared_by_descriptor_and_registry(self):
        self.unstable = True
        descriptor, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        self.assertEqual((self.counts["descriptor"], self.counts["capabilities"]), (1, 1))
        self.assertIs(descriptor.body, self.body)
        self.assertEqual(descriptor.offered_capabilities, self.offered)
        self.assertEqual(self.registries[0].current_body, descriptor.body)
        self.assertEqual(self.registries[0].list(), (CapabilitySpec(MOVE_TO),))
        result = runtime.navigate_experience(self.experience_id)
        self.assertTrue(result.ok)
        self.assertEqual((self.counts["descriptor"], self.counts["capabilities"]), (1, 1))

    def test_later_declaration_changes_after_publication_are_not_observed(self):
        descriptor, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        self.unstable = True  # every future read would return different declarations
        self.assertEqual(descriptor.offered_capabilities, self.offered)
        self.assertEqual(self.registries[0].current_body, self.body)
        result = runtime.navigate_experience(self.experience_id)
        self.assertTrue(result.ok)
        self.assertEqual((self.counts["descriptor"], self.counts["capabilities"]), (1, 1))


class TestP253DeclarationExceptions(HardeningBase):
    def test_descriptor_and_capabilities_exceptions_are_retryable(self):
        failure = RuntimeError("unreadable declaration")
        for attr in ("descriptor", "capabilities"):
            with self.subTest(attr=attr):
                replacement = property(lambda adapter: (_ for _ in ()).throw(failure))
                with patch.object(self.counted_adapter, attr, replacement):
                    self.assertIs(self.assert_rejected(cause_type=RuntimeError).__cause__, failure)
                self.assertEqual(self.counts["bind"], 0)
                self.compose.assert_not_called()
        self.onboard()
        self.assertTrue(self.bootstrap._published)


class TestP254RegistryRemainsValidator(HardeningBase):
    def test_invalid_declarations_fail_only_in_the_existing_registry(self):
        cases = (
            {"offered": (CapabilitySpec("teleport"),)},
            {"offered": (CapabilitySpec(MOVE_TO, 2),)},
            {"offered": (CapabilitySpec(MOVE_TO), CapabilitySpec(MOVE_TO))},
            {"body": BodyDescriptor("body-a", "mock_uav", 2)},
            {"offered": (GET_POSITION, MOVE_TO)},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                saved = {"offered": self.offered, "body": self.body}
                try:
                    for name, value in changes.items():
                        setattr(self, name, value)
                    self.assert_rejected(cause_type=RegistryError)
                    self.compose.assert_not_called()
                finally:
                    self.offered, self.body = saved["offered"], saved["body"]
        self.assertEqual(self.counts["registry_init"], len(cases))
        self.onboard()
        self.assertTrue(self.bootstrap._published)

    def test_descriptor_shape_validation_does_not_duplicate_registry_rules(self):
        # Shape-only validation accepts declarations the Registry will reject;
        # P2 adds no parallel capability validator.
        descriptor = self.bootstrap.HostSessionDescriptor(
            host_session_id="opaque",
            body=BodyDescriptor("body-a", "mock_uav", 1),
            offered_capabilities=(CapabilitySpec("teleport"), CapabilitySpec(MOVE_TO, 99)),
            context_source_kinds=(),
        )
        self.assertEqual(descriptor.offered_capabilities[0].name, "teleport")


class TestP255ComposeFailures(HardeningBase):
    def test_missing_self_is_retryable(self):
        self.assert_rejected(state_dir=self.root / "missing-self")
        self.assertEqual(self.compose.call_count, 1)
        self.assertEqual(self.counts["bind"], 1)
        descriptor, _ = self.onboard()
        self.assertTrue(self.bootstrap._published)
        self.assertIsNotNone(descriptor)

    def test_corrupt_self_files_fail_before_uuid_then_recover(self):
        originals = self.self_bytes()
        corrupt = {
            "identity.json": "{}",
            "memory.json": "not json at all",
            "experiences.json": "{}",
        }
        for name, content in corrupt.items():
            with self.subTest(name=name):
                (self.state / name).write_text(content, encoding="utf-8")
                try:
                    error = self.assert_rejected(check_self_bytes=False)
                    self.assertNotIsInstance(error.__cause__, self.bootstrap.OnboardingError)
                    self.uuid.assert_not_called()
                finally:
                    (self.state / name).write_bytes(originals[name])
        self.assertEqual(self.self_bytes(), originals)
        self.onboard()
        self.assertTrue(self.bootstrap._published)

    def test_agent_mismatch_between_identity_and_history_fails_closed(self):
        other = str(uuid.uuid4())
        document = {"schema_version": 1, "agent_id": other, "records": [record(agent_id=other)]}
        # Fixture generation used the patched uuid4; only onboarding calls count.
        self.uuid.reset_mock()
        (self.state / "experiences.json").write_text(json.dumps(document), encoding="utf-8")
        try:
            error = self.assert_rejected(check_self_bytes=False)
            self.assertEqual(error.__cause__.code, INVALID_EXPERIENCE)
            self.compose.assert_called_once()
            self.uuid.assert_not_called()
        finally:
            (self.state / "experiences.json").write_bytes(self.before_self["experiences.json"])
        self.assertEqual(self.self_bytes(), self.before_self)
        self.onboard()
        self.assertTrue(self.bootstrap._published)


class TestP256UuidFailure(HardeningBase):
    def test_uuid_failure_publishes_nothing_and_retries_cleanly(self):
        failure = RuntimeError("UUID source unavailable")
        self.uuid.side_effect = failure
        with self.assertRaises(self.bootstrap.OnboardingError) as caught:
            self.onboard()
        self.assertIs(caught.exception.__cause__, failure)
        self.assertFalse(self.bootstrap._published)
        self.assertEqual(self.counts["construction"], 1)  # full preparation ran
        self.assertEqual(self.uuid.call_count, 1)
        self.assertFalse(any(
            isinstance(value, self.bootstrap.HostSessionDescriptor) for value in vars(self.bootstrap).values()))
        self.assertEqual(self.self_bytes(), self.before_self)
        self.uuid.side_effect = None
        self.onboard()
        self.assertTrue(self.bootstrap._published)
        self.assertEqual(self.uuid.call_count, 2)


class TestP257DescriptorConstructionFailure(HardeningBase):
    def test_generated_session_id_garbage_cannot_publish(self):
        # str(uuid4()) normalizes arbitrary objects to strings, so the only
        # generation garbage that reaches real descriptor validation is an
        # empty/whitespace string, or an object whose str() itself fails.
        class StrBomb:
            def __str__(self):
                raise RuntimeError("str failed")

        for garbage, cause_type in (("", ValueError), ("   ", ValueError), (StrBomb(), RuntimeError)):
            with self.subTest(garbage=repr(garbage)):
                with patch("uuid.uuid4", return_value=garbage):
                    self.assert_rejected(cause_type=cause_type)
                self.assertFalse(self.bootstrap._published)
        self.onboard()
        self.assertTrue(self.bootstrap._published)


class TestP258SecondOnboarding(HardeningBase):
    def test_second_call_performs_zero_host_work_and_old_runtime_survives(self):
        descriptor, runtime = self.onboard(allowed_capabilities=(MOVE_TO,))
        self.assertTrue(self.bootstrap._published)
        before = dict(self.counts)
        provider_counts = [provider.call_count for provider in self.providers.values()]
        self.discover.reset_mock()
        self.compose.reset_mock()
        self.uuid.reset_mock()
        with patch.object(self.bootstrap, "_load_host_discovery", side_effect=AssertionError("loaded twice")):
            with self.assertRaisesRegex(self.bootstrap.OnboardingError, "already published"):
                self.onboard()
        self.assertEqual(self.counts, before)  # discovery/construction/descriptor/capabilities/registry/bind: all 0
        self.discover.assert_not_called()
        self.compose.assert_not_called()
        self.uuid.assert_not_called()
        self.assertEqual([p.call_count for p in self.providers.values()], provider_counts)
        result = runtime.navigate_experience(self.experience_id)
        self.assertTrue(result.ok)
        self.assertEqual(descriptor.body.body_id, "body-a")


class TestP259ReentrancyAndConcurrency(HardeningBase):
    def test_reentrant_preparation_is_rejected_and_outer_attempt_completes(self):
        original = self.discovery.discover_body

        def recursive(config):
            with self.assertRaisesRegex(self.bootstrap.OnboardingError, "in progress"):
                self.onboard()
            return original(config)

        with patch.object(self.discovery, "discover_body", side_effect=recursive):
            self.onboard()
        self.assertTrue(self.bootstrap._published)
        self.assertEqual(self.uuid.call_count, 1)
        with self.assertRaisesRegex(self.bootstrap.OnboardingError, "already published"):
            self.onboard()

    def test_overlapping_preparation_publishes_exactly_one_session(self):
        entered = threading.Event()
        release = threading.Event()
        results, errors = [], []
        original = self.discovery.discover_body

        def pause(config):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timed out awaiting release")
            return original(config)

        def worker():
            try:
                results.append(self.onboard(allowed_capabilities=(MOVE_TO,)))
            except Exception as exc:
                errors.append(exc)

        with patch.object(self.discovery, "discover_body", side_effect=pause):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaisesRegex(self.bootstrap.OnboardingError, "in progress"):
                    self.onboard()
                self.assertEqual(set(self.counts.values()), {0})
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        with self.assertRaisesRegex(self.bootstrap.OnboardingError, "already published"):
            self.onboard()
        _, runtime = results[0]
        self.assertTrue(runtime.navigate_experience(self.experience_id).ok)

    def test_race_storm_yields_exactly_one_session_and_one_uuid(self):
        threads_ready = threading.Barrier(8)
        results, errors = [], []

        def racer():
            try:
                threads_ready.wait(timeout=5)
                results.append(self.onboard(allowed_capabilities=(MOVE_TO,)))
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=racer) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 7)
        self.assertTrue(all(isinstance(error, self.bootstrap.OnboardingError) for error in errors))
        self.assertTrue(all("already published" in str(error) or "in progress" in str(error) for error in errors))
        self.assertEqual(self.uuid.call_count, 1)
        self.assertEqual(self.counts["bind"], 1)
        self.assertEqual(self.counts["construction"], 1)
        self.assertTrue(self.bootstrap._published)
        _, runtime = results[0]
        self.assertTrue(runtime.navigate_experience(self.experience_id).ok)


class TestP2510NoBodyEdge(HardeningBase):
    def test_repeated_no_body_never_consumes_the_publication_slot(self):
        # Frozen behavior: a valid explicit no-body configuration returns None,
        # performs zero Host work, and may repeat any number of times.
        no_body = {"schema_version": 1, "body": None, "allowed_capabilities": []}
        for _ in range(2):
            self.assertIsNone(self.onboard(body_config=no_body))
            self.assertFalse(self.bootstrap._published)
            self.assertEqual(set(self.counts.values()), {0})
            self.uuid.assert_not_called()
            self.compose.assert_not_called()
            for provider in self.providers.values():
                provider.assert_not_called()
        # After real publication the same no-body input is simply a second
        # onboarding attempt and follows the published-session rule.
        self.onboard()
        self.assertTrue(self.bootstrap._published)
        before = dict(self.counts)
        with patch.object(self.bootstrap, "_load_host_discovery", side_effect=AssertionError("loaded twice")):
            with self.assertRaisesRegex(self.bootstrap.OnboardingError, "already published"):
                self.onboard(body_config=no_body)
        self.assertEqual(self.counts, before)


class TestP2511DynamicLoadingAttackSurface(HardeningBase):
    def test_adapter_identifier_cannot_trigger_loading(self):
        hostile_names = (
            "../../evil.py", "C:\\evil.py", "module.name", "https://evil.example/x.py",
            "file:///evil.py", "os.system", "builtins", "subprocess", "mock_uav.py",
            "mock_uav; import os", "mock_uav\n", " ", "",
        )
        for name in hostile_names:
            with self.subTest(name=name):
                self.assert_rejected(
                    cause_type=self.discovery_error,
                    body_config={"schema_version": 1, "body": {"adapter": name, "body_id": "body-a"},
                                 "allowed_capabilities": []},
                )
        self.assertEqual(self.counts["construction"], 0)
        self.assertNotIn("evil", sys.modules)
        self.assertNotIn("phanes_host.evil", sys.modules)
        self.assertFalse(any("evil" in path.name for path in self.root.rglob("*")))
        # body_id is opaque declaration data: unusual values neither load nor
        # write anything, they simply reach the constructed Body as data.
        descriptor, _ = self.onboard(body_config={
            "schema_version": 1, "body": {"adapter": "mock_uav", "body_id": "../../evil"}, "allowed_capabilities": []})
        self.assertEqual(self.constructed_body_ids, ["../../evil"])
        self.assertNotIn("evil", sys.modules)
        self.assertFalse(any("evil" in path.name for path in self.root.rglob("*")))


class TestP2512RepositoryFallbackAttack(unittest.TestCase):
    """The development repository is made genuinely reachable and still cannot
    substitute for the fixed Host dependency."""

    CHILD = r'''
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

order, repo, runtime, host, state = sys.argv[1:6]
repo, runtime, host, state = map(Path, (repo, runtime, host, state))
paths = {"repo_first": [repo, runtime, host], "repo_last": [runtime, host, repo]}[order]
sys.path[:0] = [str(path) for path in paths]

import phanes
import phanes_host
evidence = {"phanes_origin": phanes.__file__, "phanes_host_origin": phanes_host.__file__}

if order == "repo_first":
    # The fallback target exists and is importable: the P0 discovery itself.
    import phanes.discovery as p0_discovery
    evidence["p0_discovery_origin"] = p0_discovery.__file__
    p0_discovery.discover_body = Mock(side_effect=AssertionError("fallback used"))

from phanes_host import p2_bootstrap
from phanes.capabilities import CapabilityRegistry
import uuid
import phanes.p1_runtime as p1_runtime

with (
    patch.object(CapabilityRegistry, "__init__", side_effect=AssertionError("registry built")),
    patch.object(p1_runtime, "compose_p1_runtime", side_effect=AssertionError("composed")),
    patch("uuid.uuid4", side_effect=AssertionError("uuid generated")),
):
    config = {"schema_version": 1, "body": {"adapter": "mock_uav", "body_id": "body-a"}, "allowed_capabilities": []}
    providers = dict(get_environment_id=lambda: "env-a", get_asserted_frame_id=lambda: "frame-a",
                     get_evaluation_time=lambda: None)
    try:
        p2_bootstrap.onboard_host_session(str(state), config, **providers)
    except p2_bootstrap.OnboardingError as exc:
        evidence["error"] = str(exc)
        evidence["cause_type"] = type(exc.__cause__).__name__
        evidence["cause_message"] = str(exc.__cause__)
    else:
        raise AssertionError("onboarding succeeded without the fixed Host dependency")

if order == "repo_first":
    evidence["p0_fallback_calls"] = p0_discovery.discover_body.call_count
    assert evidence["p0_fallback_calls"] == 0
print(json.dumps(evidence, sort_keys=True))
'''

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.a = self.root / "A"
        source_state = self.a / "state"
        identity = init_identity(source_state)
        MemoryStore.create(source_state, identity.agent_id)
        ExperienceStore.create(source_state, identity.agent_id)
        exported = export_package_v2(source_state, self.a / "package-v2")
        self.package = self.root / "B_copied-package-v2"
        shutil.copytree(exported, self.package)
        self.runtime = self.package / "runtime"
        self.state = self.root / "restored_state"
        import_package_v2(self.package, self.state)
        self.host = self.root / "trusted_host"
        host_package = self.host / "phanes_host"
        host_package.mkdir(parents=True)
        for filename in ("__init__.py", "mock_uav.py", "p2_bootstrap.py"):
            shutil.copyfile(REPO_ROOT / "phanes_host" / filename, host_package / filename)
        # Tampered/incomplete deployment: the fixed dependency is absent.
        self.assertFalse((host_package / "_p0_discovery.py").exists())
        self.a.rename(self.root / "A_unavailable")

    def run_attack(self, order):
        env = dict(os.environ)
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", self.CHILD, order,
             str(REPO_ROOT), str(self.runtime), str(self.host), str(self.state)],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_repo_first_cannot_substitute_for_the_fixed_dependency(self):
        evidence = self.run_attack("repo_first")
        self.assertTrue(Path(evidence["phanes_origin"]).is_relative_to(REPO_ROOT))
        self.assertTrue(Path(evidence["p0_discovery_origin"]).is_relative_to(REPO_ROOT / "phanes"))
        self.assertEqual(evidence["cause_type"], "ImportError")
        self.assertIn("_p0_discovery", evidence["cause_message"])
        self.assertIn("_p0_discovery", evidence["error"])

    def test_repo_last_cannot_smuggle_the_fixed_dependency(self):
        evidence = self.run_attack("repo_last")
        self.assertTrue(Path(evidence["phanes_origin"]).is_relative_to(self.runtime))
        self.assertTrue(Path(evidence["phanes_host_origin"]).is_relative_to(self.host))
        self.assertEqual(evidence["cause_type"], "ImportError")
        self.assertIn("_p0_discovery", evidence["cause_message"])


class TestP2513PersistenceLeakSearch(HardeningBase):
    def test_session_creation_persists_no_host_state(self):
        self.values.update(environment="env-leak-7f3a", frame="frame-leak-7f3a")
        descriptor, _ = self.onboard(allowed_capabilities=(MOVE_TO,))
        package = export_package_v2(self.state, self.root / "leak-package")
        package_files = {
            path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*") if path.is_file()
        }
        self_files = self.self_bytes()
        self.assertEqual(set(self.state.iterdir()), {self.state / name for name in SELF_FILES_V2})
        shared_secrets = (
            descriptor.host_session_id.encode("utf-8"),
            b"host_session_id",
            b"leak-7f3a",
            str(self.root).encode("utf-8"),
            b"p2_bootstrap",
        )
        for name, data in self_files.items():
            for secret in shared_secrets + (b"phanes_host", b"move_to", b"get_position", b"allowed_capabilities"):
                self.assertNotIn(secret, data, (name, secret))
        for name, data in package_files.items():
            for secret in shared_secrets:
                self.assertNotIn(secret, data, (name, secret))


class TestP2514PublicApiAudit(HardeningBase):
    def test_supported_p2_api_is_exactly_three_names(self):
        self.assertEqual(
            self.bootstrap.__all__,
            ("HostSessionDescriptor", "OnboardingError", "onboard_host_session"),
        )
        namespace = {}
        exec("from phanes_host.p2_bootstrap import *", namespace)
        self.assertEqual(
            set(namespace) - {"__builtins__"},
            {"HostSessionDescriptor", "OnboardingError", "onboard_host_session"},
        )
        for name in (
            "reset", "grant", "revoke", "rebind", "bind", "publish", "unpublish",
            "SessionManager", "HostSessionBinding", "CapturedAdapterView", "CapabilityRegistry",
            "registry", "runtime", "session", "compose_p1_runtime", "load_host_discovery",
        ):
            self.assertFalse(hasattr(self.bootstrap, name), name)

    def test_private_helpers_remain_private(self):
        for name in (
            "_load_host_discovery", "_CapturedAdapterView", "_P1_CONTEXT_SOURCE_KINDS",
            "_publication_lock", "_published",
        ):
            self.assertTrue(name.startswith("_"), name)
            self.assertNotIn(name, self.bootstrap.__all__)
            self.assertTrue(hasattr(self.bootstrap, name))
        self.assertFalse(hasattr(self.bootstrap, "__getattr__"))

    def test_signature_has_no_binding_or_loading_inputs(self):
        import inspect

        parameters = inspect.signature(self.bootstrap.onboard_host_session).parameters
        self.assertEqual(
            tuple(parameters),
            ("state_dir", "body_config", "allowed_capabilities",
             "get_environment_id", "get_asserted_frame_id", "get_evaluation_time"),
        )
        for name in ("state_dir", "body_config"):
            self.assertIs(parameters[name].default, inspect.Parameter.empty, name)
        self.assertEqual(parameters["allowed_capabilities"].default, ())
        for name in ("get_environment_id", "get_asserted_frame_id", "get_evaluation_time"):
            self.assertIsNone(parameters[name].default, name)

    def test_host_package_does_not_reexport_the_onboarding_api(self):
        import phanes_host

        for name in ("HostSessionDescriptor", "OnboardingError", "onboard_host_session"):
            self.assertFalse(hasattr(phanes_host, name), name)


if __name__ == "__main__":
    unittest.main()
