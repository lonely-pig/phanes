"""P1.4 request-scoped Experience gateway and zero-action boundaries."""

import ast
import copy
import sys
import unittest
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phanes.applicability import (
    ApplicabilityResult,
    ApplicabilityStatus,
    EvaluatorFailure,
    IntendedUse,
    TargetContext,
    evaluate_experience_use,
)
from phanes.experience_contracts import (
    BODY_CONTEXT_MISSING,
    BODY_MISMATCH,
    ENVIRONMENT_CONTEXT_MISSING,
    ENVIRONMENT_MISMATCH,
    EVALUATION_TIME_MISSING,
    EVALUATOR_INTERNAL_ERROR,
    EXPERIENCE_NOT_FOUND,
    FRAME_CONTEXT_MISSING,
    FRAME_MISMATCH,
    HISTORICAL_QUERY_PERMITTED,
    INTENDED_USE_NOT_ALLOWED,
    INVALID_EXPERIENCE,
    RECORD_SUPERSEDED,
    TEMPORALLY_EXPIRED,
    TEMPORAL_DEPENDENCY_UNKNOWN,
)
from phanes.experience_gateway import (
    ExperienceUseGateway,
    HistoricalExperienceResult,
    ResolvedNavigationTarget,
    TargetContextSources,
)
from phanes.experience_store import EXPERIENCE_FILENAME, ExperienceStore
from phanes.identity import init_identity
from test_experience_semantics import record


class GatewayCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "self"
        self.identity = init_identity(self.state)
        self.store = ExperienceStore.create(self.state, self.identity.agent_id, test_mode=True)
        self.current = {
            "body_instance_id": "body-b",
            "environment_id": "site-alpha",
            "frame_id": "map-alpha",
            "evaluation_time": None,
        }
        self.calls = {name: 0 for name in self.current}
        self.failure_for = None

        def source(name):
            def read():
                self.calls[name] += 1
                if self.failure_for == name:
                    raise RuntimeError(f"{name} source failed")
                return self.current[name]
            return read

        self.sources = TargetContextSources(
            get_body_instance_id=source("body_instance_id"),
            get_environment_id=source("environment_id"),
            get_asserted_frame_id=source("frame_id"),
            get_evaluation_time=source("evaluation_time"),
        )
        self.gateway = ExperienceUseGateway(self.store, self.identity.agent_id, self.sources)

    def place(self):
        item = record(agent_id=self.identity.agent_id)
        item["payload"]["place_id"] = "Alpha"
        item["dependencies"]["environment"]["id"] = "site-alpha"
        item["dependencies"]["frame"]["id"] = "map-alpha"
        return item

    def parameter(self):
        return record("body_parameter_observation", agent_id=self.identity.agent_id)

    def add(self, item):
        self.store.append_record(item)
        return item["experience_id"]

    def assert_no_target(self, result, status, reason):
        self.assertIs(type(result), ApplicabilityResult)
        self.assertEqual(result.status, status)
        self.assertIn(reason, result.reason_codes)
        self.assertNotIsInstance(result, ResolvedNavigationTarget)


class TestGatewayNavigation(GatewayCase):
    def test_positive_navigation_is_typed_request_bound_and_has_no_raw_record(self):
        item = self.place()
        item_id = self.add(item)
        before_disk = (self.state / EXPERIENCE_FILENAME).read_bytes()
        result = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assertIsInstance(result, ResolvedNavigationTarget)
        self.assertEqual((result.agent_id, result.experience_id, result.place_id, result.x, result.y), (self.identity.agent_id, item_id, "Alpha", 10, 20))
        self.assertEqual(result.target_context, TargetContext("body-b", "site-alpha", "map-alpha", None))
        self.assertEqual(self.calls, dict.fromkeys(self.calls, 1))
        self.assertFalse(any(isinstance(value, dict) for value in vars(result).values()))
        self.assertFalse(any(value is self.store for value in vars(result).values()))
        with self.assertRaises(FrozenInstanceError):
            result.x = 90
        with self.assertRaises(FrozenInstanceError):
            result.target_context.frame_id = "map-beta"
        self.assertEqual((self.state / EXPERIENCE_FILENAME).read_bytes(), before_disk)

    def test_body_environment_and_frame_mismatch_stop(self):
        item = self.place()
        item["dependencies"]["body"] = {"mode": "EXACT", "id": "body-a"}
        item_id = self.add(item)
        cases = (
            ({"body_instance_id": "body-b"}, BODY_MISMATCH),
            ({"body_instance_id": "body-a", "environment_id": "site-beta"}, ENVIRONMENT_MISMATCH),
            ({"body_instance_id": "body-a", "frame_id": "map-beta"}, FRAME_MISMATCH),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason):
                self.current.update({"body_instance_id": "body-a", "environment_id": "site-alpha", "frame_id": "map-alpha"})
                self.current.update(changes)
                self.assert_no_target(self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET), ApplicabilityStatus.INAPPLICABLE, reason)

    def test_unasserted_frame_is_missing_and_never_inferred(self):
        item_id = self.add(self.place())
        self.current["frame_id"] = None
        result = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assert_no_target(result, ApplicabilityStatus.UNKNOWN, FRAME_CONTEXT_MISSING)

    def test_temporal_expired_unknown_and_missing_time_stop(self):
        expired = self.place()
        expired["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z", "valid_until": "2026-09-29T00:00:00Z"}
        expired_id = self.add(expired)
        self.current["evaluation_time"] = "2026-09-29T00:00:00Z"
        self.assert_no_target(self.gateway.request(expired_id, IntendedUse.NAVIGATION_TARGET), ApplicabilityStatus.INAPPLICABLE, TEMPORALLY_EXPIRED)
        self.current["evaluation_time"] = None
        self.assert_no_target(self.gateway.request(expired_id, IntendedUse.NAVIGATION_TARGET), ApplicabilityStatus.UNKNOWN, EVALUATION_TIME_MISSING)
        unknown = self.place()
        unknown["dependencies"]["temporal"] = {"mode": "UNKNOWN"}
        unknown_id = self.add(unknown)
        self.assert_no_target(self.gateway.request(unknown_id, IntendedUse.NAVIGATION_TARGET), ApplicabilityStatus.UNKNOWN, TEMPORAL_DEPENDENCY_UNKNOWN)

    def test_superseded_old_id_does_not_follow_correction(self):
        old = self.place()
        old_id = self.add(old)
        correction = self.place()
        correction["payload"]["position"] = {"x": 90, "y": 90}
        correction["supersedes"] = old_id
        new_id = self.add(correction)
        self.assert_no_target(self.gateway.request(old_id, IntendedUse.NAVIGATION_TARGET), ApplicabilityStatus.INAPPLICABLE, RECORD_SUPERSEDED)
        new_result = self.gateway.request(new_id, IntendedUse.NAVIGATION_TARGET)
        self.assertIsInstance(new_result, ResolvedNavigationTarget)
        self.assertEqual((new_result.x, new_result.y), (90, 90))

    def test_same_place_records_are_selected_only_by_exact_id(self):
        first = self.place()
        second = self.place()
        second["payload"]["position"] = {"x": 90, "y": 90}
        first_id, second_id = self.add(first), self.add(second)
        first_target = self.gateway.request(first_id, IntendedUse.NAVIGATION_TARGET)
        second_target = self.gateway.request(second_id, IntendedUse.NAVIGATION_TARGET)
        self.assertEqual((first_target.x, first_target.y), (10, 20))
        self.assertEqual((second_target.x, second_target.y), (90, 90))

    def test_parameter_navigation_is_only_use_rejection(self):
        item_id = self.add(self.parameter())
        self.current.update({name: None for name in self.current})
        result = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assertEqual(result, ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, (INTENDED_USE_NOT_ALLOWED,)))

    def test_experience_cannot_fill_any_missing_current_context(self):
        item = self.place()
        item["dependencies"]["body"] = {"mode": "EXACT", "id": "body-a"}
        item["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z", "valid_until": "2026-09-29T00:00:00Z"}
        item["observed_at"] = "2026-09-28T12:00:00Z"
        item_id = self.add(item)
        self.current.update({name: None for name in self.current})
        result = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assertEqual(result, ApplicabilityResult(ApplicabilityStatus.UNKNOWN, (BODY_CONTEXT_MISSING, ENVIRONMENT_CONTEXT_MISSING, FRAME_CONTEXT_MISSING, EVALUATION_TIME_MISSING)))


class TestGatewayHistory(GatewayCase):
    def test_place_and_parameter_history_are_non_operational_with_missing_context(self):
        for item in (self.place(), self.parameter()):
            with self.subTest(kind=item["kind"]):
                item_id = self.add(item)
                self.current.update({name: None for name in self.current})
                result = self.gateway.request(item_id, IntendedUse.HISTORICAL_QUERY)
                self.assertIsInstance(result, HistoricalExperienceResult)
                self.assertNotIsInstance(result, ResolvedNavigationTarget)
                self.assertEqual(result.experience_id, item_id)
                self.assertEqual(result.applicability, ApplicabilityResult(ApplicabilityStatus.APPLICABLE, (HISTORICAL_QUERY_PERMITTED,)))
                self.assertEqual(result.record, item)

    def test_superseded_history_and_defensive_copy(self):
        old = self.place()
        old_id = self.add(old)
        correction = self.place()
        correction["supersedes"] = old_id
        self.add(correction)
        result = self.gateway.request(old_id, IntendedUse.HISTORICAL_QUERY)
        self.assertIsInstance(result, HistoricalExperienceResult)
        result.record["payload"]["position"]["x"] = 999
        self.assertEqual(self.store.document_snapshot()["records"][0], old)
        again = self.gateway.request(old_id, IntendedUse.HISTORICAL_QUERY)
        self.assertEqual(again.record, old)


class TestGatewayBoundaries(GatewayCase):
    def test_resolved_target_cannot_be_directly_constructed(self):
        forged_id = str(uuid.uuid4())
        with patch.object(self.store, "document_snapshot") as snapshot:
            with patch("phanes.experience_gateway.evaluate_experience_use") as evaluator:
                with self.assertRaises(TypeError):
                    ResolvedNavigationTarget(
                        agent_id=self.identity.agent_id,
                        experience_id=forged_id,
                        place_id="forged",
                        x=500,
                        y=600,
                        target_context=TargetContext(),
                    )
                with self.assertRaises(TypeError):
                    ResolvedNavigationTarget()
                snapshot.assert_not_called()
                evaluator.assert_not_called()
        self.assertEqual(self.calls, dict.fromkeys(self.calls, 0))

    def test_raw_intended_use_rejected_before_store_or_sources(self):
        for raw in ("historical_query", "navigation_target", "foo", None):
            with self.subTest(raw=raw):
                with patch.object(self.store, "document_snapshot") as snapshot:
                    with self.assertRaises(TypeError):
                        self.gateway.request("missing", raw)
                    snapshot.assert_not_called()
                self.assertEqual(self.calls, dict.fromkeys(self.calls, 0))

    def test_missing_id_is_selection_failure_without_provider_calls(self):
        self.add(self.place())
        result = self.gateway.request(str(uuid.uuid4()), IntendedUse.NAVIGATION_TARGET)
        self.assertIsInstance(result, EvaluatorFailure)
        self.assertEqual(result.error_code, EXPERIENCE_NOT_FOUND)
        self.assertEqual(self.calls, dict.fromkeys(self.calls, 0))

    def test_malformed_history_fails_before_context_or_resolution(self):
        item = self.place()
        item_id = self.add(item)
        invalid = self.store.document_snapshot()
        invalid["records"][0]["dependencies"]["frame"] = {"mode": "NONE"}
        with patch.object(self.store, "document_snapshot", return_value=invalid):
            result = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assertIsInstance(result, EvaluatorFailure)
        self.assertEqual(result.error_code, INVALID_EXPERIENCE)
        self.assertEqual(self.calls, dict.fromkeys(self.calls, 0))

    def test_evaluator_failure_never_resolves_a_target(self):
        item_id = self.add(self.place())
        failure = EvaluatorFailure(EVALUATOR_INTERNAL_ERROR, "simulated")
        with patch("phanes.experience_gateway.evaluate_experience_use", return_value=failure):
            result = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assertIs(result, failure)
        self.assertNotIsInstance(result, ResolvedNavigationTarget)

    def test_provider_exception_propagates_not_unknown_or_evaluator_error(self):
        item_id = self.add(self.place())
        self.failure_for = "frame_id"
        with patch("phanes.experience_gateway.evaluate_experience_use") as evaluator:
            with self.assertRaisesRegex(RuntimeError, "frame_id source failed"):
                self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
            evaluator.assert_not_called()

    def test_one_document_and_one_context_snapshot_bind_evaluation_and_target(self):
        item_id = self.add(self.place())
        first = self.store.document_snapshot()
        alternate = copy.deepcopy(first)
        alternate["records"][0]["payload"]["position"] = {"x": 90, "y": 90}
        seen = []

        def spy(document, agent_id, experience_id, target_context, intended_use):
            seen.append((document, target_context))
            return evaluate_experience_use(document, agent_id, experience_id, target_context, intended_use)

        with patch.object(self.store, "document_snapshot", side_effect=[first, alternate]) as snapshot:
            with patch("phanes.experience_gateway.evaluate_experience_use", side_effect=spy):
                result = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assertEqual(snapshot.call_count, 1)
        self.assertIs(seen[0][0], first)
        self.assertIs(result.target_context, seen[0][1])
        self.assertEqual((result.x, result.y), (10, 20))
        self.assertEqual(self.calls, dict.fromkeys(self.calls, 1))

    def test_context_and_history_are_fresh_each_request_and_gateway_restart(self):
        item_id = self.add(self.place())
        first = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assertIsInstance(first, ResolvedNavigationTarget)
        self.current["environment_id"] = "site-beta"
        second = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assert_no_target(second, ApplicabilityStatus.INAPPLICABLE, ENVIRONMENT_MISMATCH)
        self.current["environment_id"] = "site-alpha"
        self.current["frame_id"] = None
        third = self.gateway.request(item_id, IntendedUse.NAVIGATION_TARGET)
        self.assert_no_target(third, ApplicabilityStatus.UNKNOWN, FRAME_CONTEXT_MISSING)
        self.assertEqual(self.calls, dict.fromkeys(self.calls, 3))
        fresh_sources = TargetContextSources(lambda: None, lambda: "site-beta", lambda: "map-alpha", lambda: None)
        restarted = ExperienceUseGateway(self.store, self.identity.agent_id, fresh_sources)
        self.assert_no_target(restarted.request(item_id, IntendedUse.NAVIGATION_TARGET), ApplicabilityStatus.INAPPLICABLE, ENVIRONMENT_MISMATCH)

    def test_new_correction_changes_next_request_without_history_cache(self):
        old = self.place()
        old_id = self.add(old)
        self.assertIsInstance(self.gateway.request(old_id, IntendedUse.NAVIGATION_TARGET), ResolvedNavigationTarget)
        correction = self.place()
        correction["supersedes"] = old_id
        correction["payload"]["position"] = {"x": 90, "y": 90}
        new_id = self.add(correction)
        self.assert_no_target(self.gateway.request(old_id, IntendedUse.NAVIGATION_TARGET), ApplicabilityStatus.INAPPLICABLE, RECORD_SUPERSEDED)
        fresh_store = ExperienceStore.load(self.state, self.identity.agent_id)
        restarted = ExperienceUseGateway(fresh_store, self.identity.agent_id, self.sources)
        new_target = restarted.request(new_id, IntendedUse.NAVIGATION_TARGET)
        self.assertEqual((new_target.x, new_target.y), (90, 90))

    def test_no_action_imports_or_invocations_for_any_outcome(self):
        from phanes.capabilities import CapabilityRegistry
        from phanes.core import PhanesCore
        from phanes_host.mock_uav import MockUAVAdapter

        path = Path(__file__).resolve().parent.parent / "phanes" / "experience_gateway.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module)
        self.assertFalse(any(name.startswith(("phanes_host", "phanes.core", "phanes.capabilities", "phanes.discovery", "phanes.migration")) for name in imports))
        self.assertEqual({name.split(".")[0] for name in imports} - set(sys.stdlib_module_names) - {"phanes"}, set())

        place_id = self.add(self.place())
        parameter_id = self.add(self.parameter())
        with patch.object(CapabilityRegistry, "invoke", side_effect=AssertionError("Registry action called")) as registry:
            with patch.object(MockUAVAdapter, "invoke", side_effect=AssertionError("Adapter action called")) as adapter:
                with patch.object(PhanesCore, "handle_command", side_effect=AssertionError("Core action called")) as core:
                    self.assertIsInstance(self.gateway.request(place_id, IntendedUse.NAVIGATION_TARGET), ResolvedNavigationTarget)
                    self.current["frame_id"] = None
                    self.assertIsInstance(self.gateway.request(place_id, IntendedUse.NAVIGATION_TARGET), ApplicabilityResult)
                    self.current["frame_id"] = "map-alpha"
                    self.current["environment_id"] = "site-beta"
                    self.assertIsInstance(self.gateway.request(place_id, IntendedUse.NAVIGATION_TARGET), ApplicabilityResult)
                    self.assertIsInstance(self.gateway.request(parameter_id, IntendedUse.HISTORICAL_QUERY), HistoricalExperienceResult)
                    with patch("phanes.experience_gateway.evaluate_experience_use", return_value=EvaluatorFailure(EVALUATOR_INTERNAL_ERROR, "simulated")):
                        self.assertIsInstance(self.gateway.request(place_id, IntendedUse.NAVIGATION_TARGET), EvaluatorFailure)
                    registry.assert_not_called()
                    adapter.assert_not_called()
                    core.assert_not_called()


if __name__ == "__main__":
    unittest.main()
