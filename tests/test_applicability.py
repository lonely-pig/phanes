"""P1.3 pure applicability, failure, and boundary regressions."""

import ast
import copy
import sys
import unittest
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from phanes.applicability import (
    ApplicabilityResult,
    ApplicabilityStatus,
    EvaluatorFailure,
    IntendedUse,
    TargetContext,
    evaluate_experience_use,
)
import phanes.experience_contracts as contracts
from phanes.experience_contracts import (
    ALL_REQUIRED_CONSTRAINTS_MATCH,
    BODY_CONTEXT_MISSING,
    BODY_MISMATCH,
    BROKEN_EXPERIENCE_REFERENCE,
    DUPLICATE_EXPERIENCE_ID,
    ENVIRONMENT_CONTEXT_MISSING,
    ENVIRONMENT_MISMATCH,
    EVALUATION_TIME_MISSING,
    EVALUATOR_INTERNAL_ERROR,
    EXPERIENCE_NOT_FOUND,
    EXPERIENCE_REFERENCE_CYCLE,
    FRAME_CONTEXT_MISSING,
    FRAME_MISMATCH,
    HISTORICAL_QUERY_PERMITTED,
    INTENDED_USE_NOT_ALLOWED,
    INVALID_EXPERIENCE,
    RECORD_SUPERSEDED,
    SUPERSESSION_SUBJECT_MISMATCH,
    TEMPORALLY_EXPIRED,
    TEMPORALLY_NOT_YET_VALID,
    TEMPORAL_DEPENDENCY_UNKNOWN,
    UNSUPPORTED_SCHEMA_VERSION,
)
from test_experience_semantics import AGENT_ID, document, record


def context(**changes):
    values = {"body_instance_id": None, "environment_id": "env-a", "frame_id": "frame-a", "evaluation_time": None}
    values.update(changes)
    return TargetContext(**values)


def evaluate(item, target=None, use=IntendedUse.NAVIGATION_TARGET, records=()):
    return evaluate_experience_use(document(item, *records), AGENT_ID, item["experience_id"], target or context(), use)


class TestPublicBoundary(unittest.TestCase):
    def test_only_two_statuses_and_two_intended_uses(self):
        self.assertEqual({member.value for member in IntendedUse}, {"historical_query", "navigation_target"})
        self.assertEqual({member.value for member in ApplicabilityStatus}, {"APPLICABLE", "INAPPLICABLE", "UNKNOWN"})
        self.assertIs(IntendedUse("historical_query"), IntendedUse.HISTORICAL_QUERY)
        self.assertIs(IntendedUse("navigation_target"), IntendedUse.NAVIGATION_TARGET)
        with self.assertRaises(ValueError):
            IntendedUse("configuration_apply")
        for removed in ("BODY_DEPENDENCY_UNKNOWN", "ENVIRONMENT_DEPENDENCY_UNKNOWN", "FRAME_DEPENDENCY_UNKNOWN"):
            self.assertFalse(hasattr(contracts, removed))

    def test_raw_intended_uses_raise_type_error_before_validation_or_wrapping(self):
        for raw in ("historical_query", "navigation_target", "configuration_apply", "foo", None):
            with self.subTest(raw=raw):
                with patch("phanes.applicability.validate_experience_document") as validate:
                    with self.assertRaises(TypeError):
                        evaluate_experience_use({}, AGENT_ID, "missing", context(), raw)
                    validate.assert_not_called()

    def test_target_context_is_request_value_with_explicit_missing(self):
        missing = TargetContext()
        self.assertEqual(tuple(TargetContext.__dataclass_fields__), ("body_instance_id", "environment_id", "frame_id", "evaluation_time"))
        self.assertEqual((missing.body_instance_id, missing.environment_id, missing.frame_id, missing.evaluation_time), (None, None, None, None))
        with self.assertRaises(FrozenInstanceError):
            missing.frame_id = "frame-a"
        with self.assertRaises(TypeError):
            TargetContext(frame_id="")
        with self.assertRaises(ValueError):
            TargetContext(evaluation_time="2026-09-28T00:00:00+01:00")

    def test_result_is_immutable_with_tuple_reasons(self):
        result = evaluate(record())
        self.assertEqual(result, ApplicabilityResult(ApplicabilityStatus.APPLICABLE, (ALL_REQUIRED_CONSTRAINTS_MATCH,)))
        self.assertIs(type(result.reason_codes), tuple)
        with self.assertRaises(FrozenInstanceError):
            result.status = ApplicabilityStatus.UNKNOWN
        with self.assertRaises(AttributeError):
            result.reason_codes.append(FRAME_MISMATCH)
        with self.assertRaises(TypeError):
            ApplicabilityResult(ApplicabilityStatus.UNKNOWN, [FRAME_CONTEXT_MISSING])

    def test_runtime_import_boundary(self):
        path = Path(__file__).resolve().parent.parent / "phanes" / "applicability.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module.split(".")[0])
        self.assertEqual(imports - set(sys.stdlib_module_names) - {"phanes"}, set())
        source = path.read_text(encoding="utf-8")
        for forbidden in ("phanes_host", "phanes.core", "phanes.capabilities", "phanes.discovery", "phanes.migration", "jsonschema"):
            self.assertNotIn(forbidden, source)


class TestHistoricalAndUse(unittest.TestCase):
    def test_both_kinds_are_historical_even_without_current_context(self):
        for kind in ("place_observation", "body_parameter_observation"):
            with self.subTest(kind=kind):
                item = record(kind)
                result = evaluate(item, TargetContext(body_instance_id="body-b"), IntendedUse.HISTORICAL_QUERY)
                self.assertEqual(result, ApplicabilityResult(ApplicabilityStatus.APPLICABLE, (HISTORICAL_QUERY_PERMITTED,)))

    def test_superseded_place_and_parameter_remain_historical(self):
        for kind in ("place_observation", "body_parameter_observation"):
            with self.subTest(kind=kind):
                old = record(kind)
                correction = record(kind, supersedes=old["experience_id"])
                result = evaluate(old, TargetContext(), IntendedUse.HISTORICAL_QUERY, (correction,))
                self.assertEqual(result.reason_codes, (HISTORICAL_QUERY_PERMITTED,))
                self.assertEqual(result.status, ApplicabilityStatus.APPLICABLE)

    def test_body_parameter_navigation_is_only_intended_use_rejection(self):
        item = record("body_parameter_observation")
        result = evaluate(item, TargetContext(), IntendedUse.NAVIGATION_TARGET)
        self.assertEqual(result, ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, (INTENDED_USE_NOT_ALLOWED,)))
        self.assertNotIsInstance(result, EvaluatorFailure)

    def test_provenance_class_does_not_change_applicability(self):
        for source_class in ("user_statement", "adapter_observation", "test_fixture"):
            with self.subTest(source_class=source_class):
                item = record()
                item["provenance"]["source_class"] = source_class
                self.assertEqual(evaluate(item).status, ApplicabilityStatus.APPLICABLE)


class TestNavigationDependencies(unittest.TestCase):
    def test_body_none_and_exact_match(self):
        self.assertEqual(evaluate(record(), context(body_instance_id=None)).reason_codes, (ALL_REQUIRED_CONSTRAINTS_MATCH,))
        item = record()
        item["dependencies"]["body"] = {"mode": "EXACT", "id": "body-a"}
        self.assertEqual(evaluate(item, context(body_instance_id="body-a")).status, ApplicabilityStatus.APPLICABLE)

    def test_body_mismatch_and_missing(self):
        item = record()
        item["dependencies"]["body"] = {"mode": "EXACT", "id": "body-a"}
        self.assertEqual(evaluate(item, context(body_instance_id="body-b")).reason_codes, (BODY_MISMATCH,))
        self.assertEqual(evaluate(item, context(body_instance_id=None)), ApplicabilityResult(ApplicabilityStatus.UNKNOWN, (BODY_CONTEXT_MISSING,)))

    def test_environment_exact_case_sensitive_and_missing(self):
        item = record()
        self.assertEqual(evaluate(item).status, ApplicabilityStatus.APPLICABLE)
        for current in ("env-b", "ENV-A"):
            self.assertEqual(evaluate(item, context(environment_id=current)), ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, (ENVIRONMENT_MISMATCH,)))
        self.assertEqual(evaluate(item, context(environment_id=None)), ApplicabilityResult(ApplicabilityStatus.UNKNOWN, (ENVIRONMENT_CONTEXT_MISSING,)))

    def test_frame_exact_and_missing(self):
        item = record()
        self.assertEqual(evaluate(item, context(frame_id="frame-a")).status, ApplicabilityStatus.APPLICABLE)
        self.assertEqual(evaluate(item, context(frame_id="frame-b")), ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, (FRAME_MISMATCH,)))
        self.assertEqual(evaluate(item, context(frame_id=None)), ApplicabilityResult(ApplicabilityStatus.UNKNOWN, (FRAME_CONTEXT_MISSING,)))

    def test_temporal_none_does_not_need_evaluation_time(self):
        self.assertEqual(evaluate(record(), context(evaluation_time=None)).status, ApplicabilityStatus.APPLICABLE)

    def test_temporal_unknown_never_uses_observation_or_clock(self):
        item = record(observed_at="2026-09-28T00:00:00Z")
        item["dependencies"]["temporal"] = {"mode": "UNKNOWN"}
        for now in (None, "2026-09-28T00:00:00Z"):
            with self.subTest(now=now):
                self.assertEqual(evaluate(item, context(evaluation_time=now)), ApplicabilityResult(ApplicabilityStatus.UNKNOWN, (TEMPORAL_DEPENDENCY_UNKNOWN,)))

    def test_interval_is_half_open_and_missing_time_is_unknown(self):
        item = record()
        item["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z", "valid_until": "2026-09-29T00:00:00Z"}
        cases = (
            ("2026-09-27T23:59:59Z", ApplicabilityStatus.INAPPLICABLE, TEMPORALLY_NOT_YET_VALID),
            ("2026-09-28T00:00:00Z", ApplicabilityStatus.APPLICABLE, ALL_REQUIRED_CONSTRAINTS_MATCH),
            ("2026-09-28T12:00:00Z", ApplicabilityStatus.APPLICABLE, ALL_REQUIRED_CONSTRAINTS_MATCH),
            ("2026-09-29T00:00:00Z", ApplicabilityStatus.INAPPLICABLE, TEMPORALLY_EXPIRED),
            ("2026-09-30T00:00:00Z", ApplicabilityStatus.INAPPLICABLE, TEMPORALLY_EXPIRED),
            (None, ApplicabilityStatus.UNKNOWN, EVALUATION_TIME_MISSING),
        )
        for now, status, reason in cases:
            with self.subTest(now=now):
                self.assertEqual(evaluate(item, context(evaluation_time=now)), ApplicabilityResult(status, (reason,)))

    def test_high_precision_time_and_equivalent_utc_offsets(self):
        item = record()
        item["dependencies"]["temporal"] = {
            "mode": "INTERVAL",
            "valid_from": "2026-09-28T00:00:00.0000000001Z",
            "valid_until": "2026-09-28T00:00:00.0000000003+00:00",
        }
        self.assertEqual(evaluate(item, context(evaluation_time="2026-09-28T00:00:00.0000000002Z")).status, ApplicabilityStatus.APPLICABLE)
        self.assertEqual(evaluate(item, context(evaluation_time="2026-09-28T00:00:00.0000000001+00:00")).status, ApplicabilityStatus.APPLICABLE)
        self.assertEqual(evaluate(item, context(evaluation_time="2026-09-28T00:00:00.0000000003Z")).reason_codes, (TEMPORALLY_EXPIRED,))

    def test_known_mismatch_wins_over_unknown_in_frozen_order(self):
        item = record()
        item["dependencies"]["body"] = {"mode": "EXACT", "id": "body-a"}
        result = evaluate(item, context(body_instance_id="body-b", frame_id=None))
        self.assertEqual(result, ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, (BODY_MISMATCH, FRAME_CONTEXT_MISSING)))
        item["dependencies"]["body"] = {"mode": "NONE"}
        item["dependencies"]["temporal"] = {"mode": "UNKNOWN"}
        result = evaluate(item, context(environment_id="env-b"))
        self.assertEqual(result, ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, (ENVIRONMENT_MISMATCH, TEMPORAL_DEPENDENCY_UNKNOWN)))

    def test_multiple_mismatches_and_repeated_order(self):
        item = record()
        item["dependencies"]["body"] = {"mode": "EXACT", "id": "body-a"}
        item["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z", "valid_until": "2026-09-29T00:00:00Z"}
        target = context(body_instance_id="body-b", environment_id="env-b", frame_id="frame-b", evaluation_time="2026-09-29T00:00:00Z")
        expected = ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, (BODY_MISMATCH, ENVIRONMENT_MISMATCH, FRAME_MISMATCH, TEMPORALLY_EXPIRED))
        for _ in range(100):
            self.assertEqual(evaluate(item, target), expected)

    def test_superseded_old_record_collects_lifecycle_and_context_reasons(self):
        old = record()
        old["dependencies"]["body"] = {"mode": "EXACT", "id": "body-a"}
        correction = copy.deepcopy(old)
        correction["experience_id"] = str(uuid.uuid4())
        correction["supersedes"] = old["experience_id"]
        result = evaluate(old, context(body_instance_id="body-b", frame_id=None), records=(correction,))
        self.assertEqual(result, ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, (RECORD_SUPERSEDED, BODY_MISMATCH, FRAME_CONTEXT_MISSING)))
        self.assertEqual(evaluate(correction, context(body_instance_id="body-a"), records=(old,)).status, ApplicabilityStatus.APPLICABLE)

    def test_derived_parent_does_not_supply_dependencies(self):
        parent = record()
        child = record(derived_from=[parent["experience_id"]])
        child["dependencies"]["environment"]["id"] = "env-b"
        self.assertEqual(evaluate(child, records=(parent,)).reason_codes, (ENVIRONMENT_MISMATCH,))


class TestValidationAndSelection(unittest.TestCase):
    def test_validation_precedes_exact_selection(self):
        with patch("phanes.applicability._select_exact_id") as select:
            result = evaluate_experience_use({"bad": True}, AGENT_ID, "missing", context(), IntendedUse.NAVIGATION_TARGET)
        select.assert_not_called()
        self.assertEqual(result.error_code, INVALID_EXPERIENCE)

    def test_exact_id_with_same_subject_independent_records(self):
        first = record()
        second = record()
        second["dependencies"]["environment"]["id"] = "env-b"
        history = document(first, second)
        one = evaluate_experience_use(history, AGENT_ID, first["experience_id"], context(), IntendedUse.NAVIGATION_TARGET)
        two = evaluate_experience_use(history, AGENT_ID, second["experience_id"], context(), IntendedUse.NAVIGATION_TARGET)
        self.assertEqual(one.status, ApplicabilityStatus.APPLICABLE)
        self.assertEqual(two.reason_codes, (ENVIRONMENT_MISMATCH,))

    def test_missing_id_fails_selection_before_private_evaluation(self):
        with patch("phanes.applicability._evaluate_selected") as private_evaluate:
            result = evaluate_experience_use(document(record()), AGENT_ID, str(uuid.uuid4()), context(), IntendedUse.NAVIGATION_TARGET)
        private_evaluate.assert_not_called()
        self.assertIsInstance(result, EvaluatorFailure)
        self.assertEqual(result.error_code, EXPERIENCE_NOT_FOUND)

    def test_full_document_validation_failures_are_not_unknown(self):
        broken = record(derived_from=[str(uuid.uuid4())])
        a, b = record(), record()
        a["derived_from"] = [b["experience_id"]]
        b["derived_from"] = [a["experience_id"]]
        duplicate = record(experience_id=a["experience_id"])
        subject_parent = record()
        wrong_subject = record(supersedes=subject_parent["experience_id"])
        wrong_subject["payload"]["place_id"] = "other"
        cases = (
            ({"bad": True}, INVALID_EXPERIENCE),
            (dict(document(a), schema_version=2), UNSUPPORTED_SCHEMA_VERSION),
            (document(broken), BROKEN_EXPERIENCE_REFERENCE),
            (document(a, b), EXPERIENCE_REFERENCE_CYCLE),
            (document(a, duplicate), DUPLICATE_EXPERIENCE_ID),
            (document(subject_parent, wrong_subject), SUPERSESSION_SUBJECT_MISMATCH),
            (document(record(agent_id=str(uuid.uuid4()))), INVALID_EXPERIENCE),
        )
        for history, code in cases:
            with self.subTest(code=code):
                result = evaluate_experience_use(history, AGENT_ID, a["experience_id"], context(), IntendedUse.NAVIGATION_TARGET)
                self.assertIsInstance(result, EvaluatorFailure)
                self.assertEqual(result.error_code, code)

    def test_unexpected_private_error_maps_to_internal_failure(self):
        item = record()
        with patch("phanes.applicability._temporal_reason", side_effect=RuntimeError("boom")):
            result = evaluate(item)
        self.assertIsInstance(result, EvaluatorFailure)
        self.assertEqual(result.error_code, EVALUATOR_INTERNAL_ERROR)
        self.assertIn("boom", result.diagnostic)

    def test_no_input_mutation_and_no_cached_result(self):
        item = record()
        history = document(item)
        original = copy.deepcopy(history)
        matching = context()
        changed = context(environment_id="env-b")
        missing = context(frame_id=None)
        self.assertEqual(evaluate_experience_use(history, AGENT_ID, item["experience_id"], matching, IntendedUse.NAVIGATION_TARGET).status, ApplicabilityStatus.APPLICABLE)
        self.assertEqual(evaluate_experience_use(history, AGENT_ID, item["experience_id"], changed, IntendedUse.NAVIGATION_TARGET).status, ApplicabilityStatus.INAPPLICABLE)
        self.assertEqual(evaluate_experience_use(history, AGENT_ID, item["experience_id"], missing, IntendedUse.NAVIGATION_TARGET).status, ApplicabilityStatus.UNKNOWN)
        self.assertEqual(history, original)
        self.assertEqual(matching, context())


if __name__ == "__main__":
    unittest.main()
