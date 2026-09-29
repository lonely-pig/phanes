"""P1.5 exact-ID operation, current-context and Host-authority integration."""

import contextlib
import inspect
import io
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phanes import __main__ as cli
from phanes.applicability import ApplicabilityResult, ApplicabilityStatus, EvaluatorFailure
from phanes.capabilities import CapabilityRegistry
from phanes.contracts import ADAPTER_ERROR, CAPABILITY_UNAVAILABLE, INVALID_ARGUMENT, MOVE_TO, NO_BODY, ContractError, InvocationResult
from phanes.experience_contracts import (
    BODY_CONTEXT_MISSING, BODY_MISMATCH, ENVIRONMENT_CONTEXT_MISSING,
    ENVIRONMENT_MISMATCH, EVALUATION_TIME_MISSING, EVALUATOR_INTERNAL_ERROR,
    EXPERIENCE_NOT_FOUND, FRAME_CONTEXT_MISSING, FRAME_MISMATCH,
    INTENDED_USE_NOT_ALLOWED, INVALID_EXPERIENCE, RECORD_SUPERSEDED,
    TEMPORALLY_EXPIRED, TEMPORAL_DEPENDENCY_UNKNOWN,
)
from phanes.experience_gateway import ExperienceUseGateway, HistoricalExperienceResult, TargetContextSources
from phanes.experience_store import ExperienceStore
from phanes.identity import init_identity
from phanes.memory import MemoryStore
from phanes.p1_runtime import compose_p1_runtime
from phanes_host.mock_uav import MockUAVAdapter
from test_experience_semantics import record


class OperationCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "self"
        self.identity = init_identity(self.state)
        MemoryStore.create(self.state, self.identity.agent_id)
        self.store = ExperienceStore.create(self.state, self.identity.agent_id, test_mode=True)
        self.current = {"environment": "env-a", "frame": "frame-a", "time": None}
        self.provider_calls = {key: 0 for key in self.current}

        def provider(key):
            def read():
                self.provider_calls[key] += 1
                return self.current[key]
            return read

        self.providers = dict(
            get_environment_id=provider("environment"),
            get_asserted_frame_id=provider("frame"),
            get_evaluation_time=provider("time"),
        )

    def place(self, **changes):
        return record(agent_id=self.identity.agent_id, **changes)

    def add(self, item):
        self.store.append_record(item)
        return item["experience_id"]

    def host(self, allowed=(MOVE_TO,), body_id="body-a"):
        registry = CapabilityRegistry()
        adapter = MockUAVAdapter(body_id)
        if body_id is not None:
            registry.bind(adapter, allowed)
        return compose_p1_runtime(self.state, registry, **self.providers), registry, adapter

    def execute_counted(self, runtime, registry, adapter, experience_id):
        with patch.object(registry, "list", wraps=registry.list) as listed:
            with patch.object(registry, "invoke", wraps=registry.invoke) as invoked:
                with patch.object(adapter, "invoke", wraps=adapter.invoke) as adapted:
                    result = runtime.navigate_experience(experience_id)
        return result, (listed.call_count, invoked.call_count, adapted.call_count)

    def assert_blocked(self, result, calls, status, reason):
        self.assertIsInstance(result, ApplicabilityResult)
        self.assertEqual(result.status, status)
        self.assertIn(reason, result.reason_codes)
        self.assertEqual(calls, (0, 0, 0))


class TestOperationalGates(OperationCase):
    def test_authorized_exact_id_moves_once(self):
        item = self.place()
        item["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z", "valid_until": "2026-09-30T00:00:00Z"}
        item_id = self.add(item)
        self.current["time"] = "2026-09-29T00:00:00Z"
        runtime, registry, adapter = self.host()
        result, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertEqual(calls, (1, 1, 1))
        self.assertEqual(result, InvocationResult.success({"x": 10, "y": 20}))
        self.assertEqual((adapter._x, adapter._y), (10, 20))
        self.assertEqual(self.provider_calls, dict.fromkeys(self.provider_calls, 1))

    def test_applicable_but_current_host_unprivileged(self):
        item_id = self.add(self.place())
        runtime, registry, adapter = self.host(allowed=())
        result, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertEqual(result.error.code, CAPABILITY_UNAVAILABLE)
        self.assertEqual(calls, (1, 0, 0))
        self.assertEqual((adapter._x, adapter._y), (0, 0))

    def test_no_body_still_fails_after_gateway(self):
        item_id = self.add(self.place())
        runtime, registry, adapter = self.host(body_id=None)
        result, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertEqual(result.error.code, NO_BODY)
        self.assertEqual(calls, (0, 0, 0))

    def test_all_mismatches_and_superseded_stop_before_registry_list(self):
        cases = (
            ("body", BODY_MISMATCH), ("environment", ENVIRONMENT_MISMATCH),
            ("frame", FRAME_MISMATCH), ("expired", TEMPORALLY_EXPIRED),
            ("superseded", RECORD_SUPERSEDED),
        )
        for case, reason in cases:
            with self.subTest(case=case):
                item = self.place()
                if case == "body":
                    item["dependencies"]["body"] = {"mode": "EXACT", "id": "other-body"}
                elif case == "environment":
                    self.current["environment"] = "elsewhere"
                elif case == "frame":
                    self.current["frame"] = "other-frame"
                elif case == "expired":
                    item["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z", "valid_until": "2026-09-29T00:00:00Z"}
                    self.current["time"] = "2026-09-29T00:00:00Z"
                item_id = self.add(item)
                if case == "superseded":
                    replacement = self.place()
                    replacement["supersedes"] = item_id
                    self.add(replacement)
                runtime, registry, adapter = self.host()
                result, calls = self.execute_counted(runtime, registry, adapter, item_id)
                self.assert_blocked(result, calls, ApplicabilityStatus.INAPPLICABLE, reason)
                self.current.update(environment="env-a", frame="frame-a", time=None)

    def test_all_missing_context_and_unknown_temporal_stop(self):
        cases = (
            ("body", BODY_CONTEXT_MISSING), ("environment", ENVIRONMENT_CONTEXT_MISSING),
            ("frame", FRAME_CONTEXT_MISSING), ("time", EVALUATION_TIME_MISSING),
            ("temporal", TEMPORAL_DEPENDENCY_UNKNOWN),
        )
        for case, reason in cases:
            with self.subTest(case=case):
                item = self.place()
                body_id = "body-a"
                if case == "body":
                    item["dependencies"]["body"] = {"mode": "EXACT", "id": "body-a"}
                    body_id = None
                elif case == "environment":
                    self.current["environment"] = None
                elif case == "frame":
                    self.current["frame"] = None
                elif case == "time":
                    item["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z", "valid_until": "2026-09-29T00:00:00Z"}
                else:
                    item["dependencies"]["temporal"] = {"mode": "UNKNOWN"}
                item_id = self.add(item)
                runtime, registry, adapter = self.host(body_id=body_id)
                result, calls = self.execute_counted(runtime, registry, adapter, item_id)
                self.assert_blocked(result, calls, ApplicabilityStatus.UNKNOWN, reason)
                self.current.update(environment="env-a", frame="frame-a", time=None)

    def test_missing_id_invalid_history_and_evaluator_failure(self):
        item_id = self.add(self.place())
        runtime, registry, adapter = self.host()
        result, calls = self.execute_counted(runtime, registry, adapter, str(uuid.uuid4()))
        self.assertEqual((result.error_code, calls), (EXPERIENCE_NOT_FOUND, (0, 0, 0)))
        invalid = runtime._gateway._store.document_snapshot()
        invalid["records"][0]["dependencies"]["frame"] = {"mode": "NONE"}
        with patch.object(runtime._gateway._store, "document_snapshot", return_value=invalid):
            result, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertEqual((result.error_code, calls), (INVALID_EXPERIENCE, (0, 0, 0)))
        failure = EvaluatorFailure(EVALUATOR_INTERNAL_ERROR, "simulated")
        with patch("phanes.experience_gateway.evaluate_experience_use", return_value=failure):
            result, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertIs(result, failure)
        self.assertEqual(calls, (0, 0, 0))

    def test_provider_exception_is_not_unknown_and_never_calls_registry(self):
        item_id = self.add(self.place())
        runtime, registry, adapter = self.host()
        def failed():
            raise RuntimeError("frame source failed")
        runtime._gateway._sources = TargetContextSources(lambda: "body-a", lambda: "env-a", failed, lambda: None)
        with patch.object(registry, "list", wraps=registry.list) as listed:
            with patch.object(registry, "invoke", wraps=registry.invoke) as invoked:
                with patch.object(adapter, "invoke", wraps=adapter.invoke) as adapted:
                    with self.assertRaisesRegex(RuntimeError, "frame source failed"):
                        runtime.navigate_experience(item_id)
        self.assertEqual((listed.call_count, invoked.call_count, adapted.call_count), (0, 0, 0))

    def test_parameter_navigation_blocked_but_history_remains_readable(self):
        item_id = self.add(record("body_parameter_observation", agent_id=self.identity.agent_id))
        runtime, registry, adapter = self.host()
        result, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertEqual(result.reason_codes, (INTENDED_USE_NOT_ALLOWED,))
        self.assert_blocked(result, calls, ApplicabilityStatus.INAPPLICABLE, INTENDED_USE_NOT_ALLOWED)
        from phanes.applicability import IntendedUse
        self.assertIsInstance(runtime._gateway.request(item_id, IntendedUse.HISTORICAL_QUERY), HistoricalExperienceResult)

    def test_same_place_exact_ids_and_no_successor_follow(self):
        first = self.place()
        first_id = self.add(first)
        second = self.place()
        second["payload"]["position"] = {"x": 90, "y": 91}
        second_id = self.add(second)
        runtime, registry, adapter = self.host()
        first_result, first_calls = self.execute_counted(runtime, registry, adapter, first_id)
        second_result, second_calls = self.execute_counted(runtime, registry, adapter, second_id)
        self.assertEqual(first_result.data, {"x": 10, "y": 20})
        self.assertEqual(second_result.data, {"x": 90, "y": 91})
        self.assertEqual((first_calls, second_calls), ((1, 1, 1), (1, 1, 1)))
        correction = self.place()
        correction["supersedes"] = first_id
        correction["payload"]["position"] = {"x": 50, "y": 60}
        correction_id = self.add(correction)
        restarted, registry2, adapter2 = self.host()
        old, calls = self.execute_counted(restarted, registry2, adapter2, first_id)
        self.assert_blocked(old, calls, ApplicabilityStatus.INAPPLICABLE, RECORD_SUPERSEDED)
        new, calls = self.execute_counted(restarted, registry2, adapter2, correction_id)
        self.assertEqual((new.data, calls), ({"x": 50, "y": 60}, (1, 1, 1)))

    def test_context_change_and_stale_target_cannot_replay(self):
        item_id = self.add(self.place())
        runtime, registry, adapter = self.host()
        first, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertTrue(first.ok)
        self.assertEqual(calls, (1, 1, 1))
        self.current["frame"] = None
        second, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assert_blocked(second, calls, ApplicabilityStatus.UNKNOWN, FRAME_CONTEXT_MISSING)
        self.assertFalse(hasattr(runtime, "execute_resolved_target"))
        self.assertFalse(hasattr(runtime, "handle_command"))
        self.assertEqual((adapter._x, adapter._y), (10, 20))
        self.assertEqual(self.provider_calls, dict.fromkeys(self.provider_calls, 2))

    def test_public_operation_accepts_only_id_not_old_target_or_raw_value(self):
        from phanes.applicability import IntendedUse
        item_id = self.add(self.place())
        runtime, registry, adapter = self.host()
        old_target = runtime._gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assertEqual(tuple(inspect.signature(runtime.navigate_experience).parameters), ("experience_id",))
        for invalid in (old_target, {"x": 10, "y": 20}, self.place(), None):
            with self.subTest(invalid_type=type(invalid).__name__):
                with patch.object(runtime._gateway, "request") as request:
                    with patch.object(registry, "list") as listed:
                        with patch.object(registry, "invoke") as invoked:
                            with patch.object(adapter, "invoke") as adapted:
                                with self.assertRaises(TypeError):
                                    runtime.navigate_experience(invalid)
                request.assert_not_called()
                listed.assert_not_called()
                invoked.assert_not_called()
                adapted.assert_not_called()

    def test_new_host_does_not_inherit_authority(self):
        item_id = self.add(self.place())
        host_a, registry_a, adapter_a = self.host(allowed=(MOVE_TO,))
        result_a, calls_a = self.execute_counted(host_a, registry_a, adapter_a, item_id)
        host_b, registry_b, adapter_b = self.host(allowed=())
        result_b, calls_b = self.execute_counted(host_b, registry_b, adapter_b, item_id)
        self.assertTrue(result_a.ok)
        self.assertEqual(calls_a, (1, 1, 1))
        self.assertEqual(result_b.error.code, CAPABILITY_UNAVAILABLE)
        self.assertEqual(calls_b, (1, 0, 0))
        self.assertEqual((adapter_b._x, adapter_b._y), (0, 0))

    def test_huge_integer_complete_path_and_registry_validation(self):
        item = self.place()
        item["payload"]["position"] = {"x": 10**400, "y": -(10**400)}
        item_id = self.add(item)
        runtime, registry, adapter = self.host()
        result, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertEqual(calls, (1, 1, 1))
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"x": 10**400, "y": -(10**400)})
        self.assertEqual((adapter._x, adapter._y), (10**400, -(10**400)))

    def test_registry_argument_failure_never_calls_adapter_or_reports_success(self):
        item_id = self.add(self.place())
        runtime, registry, adapter = self.host()
        with patch("phanes.capabilities.validate_capability_args", side_effect=ContractError("rejected")):
            result, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertEqual(result.error.code, INVALID_ARGUMENT)
        self.assertEqual(calls, (1, 1, 0))
        self.assertEqual((adapter._x, adapter._y), (0, 0))

    def test_adapter_failure_is_not_success_and_is_not_retried(self):
        item_id = self.add(self.place())
        runtime, registry, adapter = self.host()
        with patch.object(adapter, "invoke", side_effect=RuntimeError("adapter failed")) as adapted:
            with patch.object(registry, "list", wraps=registry.list) as listed:
                with patch.object(registry, "invoke", wraps=registry.invoke) as invoked:
                    result = runtime.navigate_experience(item_id)
        self.assertEqual(result.error.code, ADAPTER_ERROR)
        self.assertFalse(result.ok)
        self.assertEqual((listed.call_count, invoked.call_count, adapted.call_count), (1, 1, 1))

    def test_p1_runtime_never_uses_legacy_memory_lookup(self):
        item_id = self.add(self.place())
        runtime, registry, adapter = self.host()
        with patch.object(MemoryStore, "lookup_place", side_effect=AssertionError("legacy bypass")):
            result, calls = self.execute_counted(runtime, registry, adapter, item_id)
        self.assertTrue(result.ok)
        self.assertEqual(calls, (1, 1, 1))

    def test_p0_cli_rejects_complete_p1_self_before_legacy_command(self):
        self.add(self.place())
        with patch("phanes.__main__.discover_body", side_effect=AssertionError("discovery started")):
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                rc = cli.main(["run", "--state", str(self.state)])
        self.assertEqual(rc, 1)
        self.assertIn("P1 Experience Self", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
