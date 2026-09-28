"""P1.1 Experience structural validation tests (Architecture Freeze v0.2,
sections 6, 7, 8.1, 10.1).

Covers: the ExperienceDocument and record exact-key structure, the two fixed
kinds and their payloads, provenance, the global dependency vocabulary,
canonical UUID and UTC RFC 3339 shapes, finite-number edge cases, stable
error codes, input immutability, the structural/semantic boundary
(structurally valid but kind-table-disallowed dependency combinations must
PASS here and be rejected later by P1.2), the shared conformance fixtures,
and the normative JSON Schema invariants.
"""

import ast
import copy
import json
import sys
import unittest
import uuid
from pathlib import Path

from phanes import experience_contracts as ec
from phanes.experience_validation import (
    ExperienceValidationError,
    validate_experience_document_structure,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "experience_v1"
SCHEMA_PATH = REPO_ROOT / "schemas" / "phanes-experience-v1.schema.json"

AGENT_ID = "7d4d40b9-9f8a-40b1-80ec-cb0333bdb2b7"
RECORDED_AT = "2026-09-28T00:00:00Z"
OBSERVED_AT = "2026-09-27T12:30:00Z"


def _uid() -> str:
    return str(uuid.uuid4())


_MISSING = object()


def _spatial(mode: str = "NONE", **extra) -> dict:
    dep = {"mode": mode}
    dep.update(extra)
    return dep


def _dependencies(body=_MISSING, environment=_MISSING, frame=_MISSING, temporal=_MISSING) -> dict:
    return {
        "body": _spatial() if body is _MISSING else body,
        "environment": _spatial() if environment is _MISSING else environment,
        "frame": _spatial() if frame is _MISSING else frame,
        "temporal": _spatial() if temporal is _MISSING else temporal,
    }


def _place_record(**overrides) -> dict:
    record = {
        "experience_id": _uid(),
        "agent_id": AGENT_ID,
        "kind": "place_observation",
        "payload": {"place_id": "dock-a", "position": {"x": 10, "y": 20}},
        "provenance": {"source_class": "user_statement", "source_ref": "cli:note:1"},
        "dependencies": _dependencies(),
        "recorded_at": RECORDED_AT,
        "observed_at": OBSERVED_AT,
        "derived_from": [],
        "supersedes": None,
    }
    record.update(overrides)
    return record


def _body_param_record(**overrides) -> dict:
    record = {
        "experience_id": _uid(),
        "agent_id": AGENT_ID,
        "kind": "body_parameter_observation",
        "payload": {"parameter_name": "ROLL_KP", "value": 0.18, "unit": "mock-unit"},
        "provenance": {"source_class": "adapter_observation", "source_ref": "invoke:42"},
        "dependencies": _dependencies(body=_spatial("EXACT", id="mock-uav-A")),
        "recorded_at": RECORDED_AT,
        "observed_at": None,
        "derived_from": [],
        "supersedes": None,
    }
    record.update(overrides)
    return record


def _document(records=_MISSING, **overrides) -> dict:
    document = {
        "schema_version": 1,
        "agent_id": AGENT_ID,
        "records": [] if records is _MISSING else records,
    }
    document.update(overrides)
    return document


class ValidationCase(unittest.TestCase):
    def assertValid(self, document) -> None:
        validate_experience_document_structure(document)

    def assertInvalid(self, document, code: str = ec.INVALID_EXPERIENCE) -> None:
        with self.assertRaises(ExperienceValidationError) as ctx:
            validate_experience_document_structure(document)
        self.assertEqual(ctx.exception.code, code)


class TestContractModule(ValidationCase):
    def test_error_codes_are_stable(self):
        self.assertEqual(ec.INVALID_EXPERIENCE, "INVALID_EXPERIENCE")
        self.assertEqual(ec.UNSUPPORTED_SCHEMA_VERSION, "UNSUPPORTED_SCHEMA_VERSION")

    def test_frozen_vocabulary(self):
        self.assertEqual(ec.EXPERIENCE_SCHEMA_VERSION, 1)
        self.assertEqual(
            ec.EXPERIENCE_KINDS,
            frozenset({"place_observation", "body_parameter_observation"}),
        )
        self.assertEqual(
            ec.PROVENANCE_SOURCE_CLASSES,
            frozenset({"user_statement", "adapter_observation", "test_fixture"}),
        )
        self.assertEqual(
            ec.SPATIAL_DEPENDENCY_MODES, frozenset({"NONE", "EXACT", "UNKNOWN"})
        )
        self.assertEqual(
            ec.TEMPORAL_DEPENDENCY_MODES, frozenset({"NONE", "INTERVAL", "UNKNOWN"})
        )

    def test_experience_modules_import_only_stdlib_and_phanes(self):
        for module_name in ("experience_contracts.py", "experience_validation.py"):
            tree = ast.parse(
                (REPO_ROOT / "phanes" / module_name).read_text(encoding="utf-8")
            )
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])
            non_stdlib = imported - set(sys.stdlib_module_names) - {"phanes"}
            self.assertEqual(non_stdlib, set(), f"non-stdlib imports in {module_name}")
            self.assertNotIn("phanes_host", imported)

    def test_error_carries_code_and_diagnostic(self):
        with self.assertRaises(ExperienceValidationError) as ctx:
            validate_experience_document_structure(_document(schema_version=2))
        err = ctx.exception
        self.assertEqual(err.code, ec.UNSUPPORTED_SCHEMA_VERSION)
        self.assertIn("schema_version", err.diagnostic)
        self.assertIn(ec.UNSUPPORTED_SCHEMA_VERSION, str(err))


class TestDocumentStructure(ValidationCase):
    def test_empty_document_is_valid(self):
        self.assertValid(_document())

    def test_non_object_document_rejected(self):
        for bad in ([], "x", 1, None, True):
            with self.subTest(value=bad):
                self.assertInvalid(bad)

    def test_missing_document_fields_rejected(self):
        for field in ("schema_version", "agent_id", "records"):
            with self.subTest(field=field):
                doc = _document()
                del doc[field]
                self.assertInvalid(doc)

    def test_extra_document_field_rejected(self):
        self.assertInvalid(_document(comment="extra"))

    def test_schema_version_accepts_json_number_one(self):
        # JSON Schema numeric equality: 1 and 1.0 are the same JSON number.
        self.assertValid(_document(schema_version=1))
        self.assertValid(_document(schema_version=1.0))

    def test_unsupported_schema_version_fails_closed(self):
        for bad in (0, 2, -1, 1.5, "1", True, False, None):
            with self.subTest(version=bad):
                self.assertInvalid(_document(schema_version=bad), ec.UNSUPPORTED_SCHEMA_VERSION)

    def test_document_agent_id_must_be_canonical_uuid(self):
        for bad in (123, None, "not-a-uuid", AGENT_ID.upper(), AGENT_ID.replace("-", "")):
            with self.subTest(agent_id=bad):
                self.assertInvalid(_document(agent_id=bad))

    def test_records_must_be_array(self):
        for bad in ({}, "records", None, 1):
            with self.subTest(records=bad):
                self.assertInvalid(_document(records=bad))

    def test_multi_record_document_valid(self):
        self.assertValid(_document([_place_record(), _body_param_record()]))


class TestRecordStructure(ValidationCase):
    def test_missing_each_record_field_rejected(self):
        for field in sorted(ec.RECORD_KEYS):
            with self.subTest(field=field):
                record = _place_record()
                del record[field]
                self.assertInvalid(_document([record]))

    def test_extra_record_field_rejected(self):
        record = _place_record(confidence=0.9)
        self.assertInvalid(_document([record]))

    def test_non_object_record_rejected(self):
        for bad in ([], "x", 1, None):
            with self.subTest(record=bad):
                self.assertInvalid(_document([bad]))

    def test_invalid_kind_rejected(self):
        for bad in ("generic_observation", "configuration", "skill", "event", "", 1, None):
            with self.subTest(kind=bad):
                self.assertInvalid(_document([_place_record(kind=bad)]))

    def test_wrong_field_types_rejected(self):
        self.assertInvalid(_document([_place_record(experience_id=123)]))
        self.assertInvalid(_document([_place_record(agent_id=None)]))
        self.assertInvalid(_document([_place_record(payload="not-an-object")]))
        self.assertInvalid(_document([_place_record(provenance=[])]))
        self.assertInvalid(_document([_place_record(dependencies=None)]))
        self.assertInvalid(_document([_place_record(recorded_at=None)]))


class TestPlacePayload(ValidationCase):
    def _doc_with_payload(self, payload) -> dict:
        return _document([_place_record(payload=payload)])

    def test_missing_payload_fields_rejected(self):
        for field in ("place_id", "position"):
            with self.subTest(field=field):
                payload = {"place_id": "dock-a", "position": {"x": 10, "y": 20}}
                del payload[field]
                self.assertInvalid(self._doc_with_payload(payload))

    def test_extra_payload_field_rejected(self):
        payload = {"place_id": "dock-a", "position": {"x": 10, "y": 20}, "z": 0}
        self.assertInvalid(self._doc_with_payload(payload))

    def test_place_id_must_be_non_empty_string(self):
        for bad in ("", 1, None, ["dock-a"]):
            with self.subTest(place_id=bad):
                payload = {"place_id": bad, "position": {"x": 10, "y": 20}}
                self.assertInvalid(self._doc_with_payload(payload))

    def test_place_id_is_opaque_not_normalized(self):
        # Whitespace-bearing identifiers are opaque and structurally legal.
        payload = {"place_id": " dock-a ", "position": {"x": 10, "y": 20}}
        self.assertValid(self._doc_with_payload(payload))

    def test_position_must_be_object_with_exact_keys(self):
        for bad in ({"x": 10}, {"x": 10, "y": 20, "z": 30}, [10, 20], None, "pos"):
            with self.subTest(position=bad):
                payload = {"place_id": "dock-a", "position": bad}
                self.assertInvalid(self._doc_with_payload(payload))

    def test_position_rejects_bool_nan_infinity_and_strings(self):
        for axis in ("x", "y"):
            for bad in (True, False, float("nan"), float("inf"), float("-inf"), "10", None):
                with self.subTest(axis=axis, value=bad):
                    position = {"x": 10, "y": 20}
                    position[axis] = bad
                    payload = {"place_id": "dock-a", "position": position}
                    self.assertInvalid(self._doc_with_payload(payload))

    def test_position_accepts_finite_int_and_float(self):
        for value in (0, -3, 2.5, -0.75, 10**6):
            position = {"x": value, "y": value}
            self.assertValid(self._doc_with_payload({"place_id": "p", "position": position}))

    def test_position_accepts_huge_integers_without_crash(self):
        # Arbitrary-size Python ints are finite JSON numbers; validation
        # must not implicitly float-convert them (OverflowError regression).
        for value in (10**100, -(10**100), 2**1024, -(2**1024)):
            with self.subTest(value_bits=value.bit_length()):
                position = {"x": value, "y": value}
                self.assertValid(self._doc_with_payload({"place_id": "p", "position": position}))


class TestBodyParameterPayload(ValidationCase):
    def _doc_with_payload(self, payload) -> dict:
        return _document([_body_param_record(payload=payload)])

    def test_valid_body_parameter_record(self):
        self.assertValid(_document([_body_param_record()]))

    def test_missing_payload_fields_rejected(self):
        for field in ("parameter_name", "value", "unit"):
            with self.subTest(field=field):
                payload = {"parameter_name": "ROLL_KP", "value": 0.18, "unit": "mock-unit"}
                del payload[field]
                self.assertInvalid(self._doc_with_payload(payload))

    def test_extra_payload_field_rejected(self):
        payload = {"parameter_name": "ROLL_KP", "value": 0.18, "unit": "mock-unit", "min": 0.0}
        self.assertInvalid(self._doc_with_payload(payload))

    def test_parameter_name_and_unit_must_be_non_empty_strings(self):
        for field in ("parameter_name", "unit"):
            for bad in ("", 1, None):
                with self.subTest(field=field, value=bad):
                    payload = {"parameter_name": "ROLL_KP", "value": 0.18, "unit": "mock-unit"}
                    payload[field] = bad
                    self.assertInvalid(self._doc_with_payload(payload))

    def test_value_rejects_bool_nan_infinity_and_strings(self):
        for bad in (True, False, float("nan"), float("inf"), float("-inf"), "0.18", None):
            with self.subTest(value=bad):
                payload = {"parameter_name": "ROLL_KP", "value": bad, "unit": "mock-unit"}
                self.assertInvalid(self._doc_with_payload(payload))

    def test_value_accepts_huge_integers_without_crash(self):
        for value in (10**100, -(10**100)):
            with self.subTest(value_bits=value.bit_length()):
                payload = {"parameter_name": "ROLL_KP", "value": value, "unit": "mock-unit"}
                self.assertValid(self._doc_with_payload(payload))

    def test_payload_of_wrong_kind_rejected(self):
        self.assertInvalid(
            _document([_place_record(payload={"parameter_name": "P", "value": 1, "unit": "u"})])
        )
        self.assertInvalid(
            _document(
                [_body_param_record(payload={"place_id": "p", "position": {"x": 1, "y": 2}})]
            )
        )


class TestProvenance(ValidationCase):
    def _doc_with_provenance(self, provenance) -> dict:
        return _document([_place_record(provenance=provenance)])

    def test_all_source_classes_valid(self):
        for source_class in sorted(ec.PROVENANCE_SOURCE_CLASSES):
            with self.subTest(source_class=source_class):
                self.assertValid(
                    self._doc_with_provenance(
                        {"source_class": source_class, "source_ref": "ref-1"}
                    )
                )

    def test_invalid_source_class_rejected(self):
        for bad in ("admin_override", "verified", "", 1, None):
            with self.subTest(source_class=bad):
                self.assertInvalid(
                    self._doc_with_provenance({"source_class": bad, "source_ref": "ref-1"})
                )

    def test_missing_or_empty_source_ref_rejected(self):
        self.assertInvalid(self._doc_with_provenance({"source_class": "user_statement"}))
        for bad in ("", 1, None):
            with self.subTest(source_ref=bad):
                self.assertInvalid(
                    self._doc_with_provenance(
                        {"source_class": "user_statement", "source_ref": bad}
                    )
                )

    def test_extra_provenance_fields_rejected(self):
        # Provenance is a source assertion; no confidence/trust fields exist.
        for extra in ("confidence", "signature", "verified", "trusted"):
            with self.subTest(extra=extra):
                provenance = {
                    "source_class": "user_statement",
                    "source_ref": "ref-1",
                    extra: True,
                }
                self.assertInvalid(self._doc_with_provenance(provenance))


class TestDependencyShapes(ValidationCase):
    def _doc_with_deps(self, deps, record_factory=_place_record) -> dict:
        return _document([record_factory(dependencies=deps)])

    def test_all_global_spatial_modes_valid_on_all_dimensions(self):
        for mode in ("NONE", "EXACT", "UNKNOWN"):
            dep = _spatial("EXACT", id="opaque-id") if mode == "EXACT" else _spatial(mode)
            for dimension in ("body", "environment", "frame"):
                with self.subTest(mode=mode, dimension=dimension):
                    self.assertValid(self._doc_with_deps(_dependencies(**{dimension: dep})))

    def test_all_global_temporal_modes_valid(self):
        for temporal in (
            _spatial("NONE"),
            _spatial("UNKNOWN"),
            {
                "mode": "INTERVAL",
                "valid_from": "2026-09-28T00:00:00Z",
                "valid_until": "2026-09-29T00:00:00Z",
            },
        ):
            with self.subTest(temporal=temporal):
                self.assertValid(self._doc_with_deps(_dependencies(temporal=temporal)))

    def test_interval_bounds_are_not_ordered_structurally(self):
        # valid_from >= valid_until is semantic validation (Freeze 8.3),
        # not structural: it must remain structurally valid in P1.1.
        temporal = {
            "mode": "INTERVAL",
            "valid_from": "2026-09-29T00:00:00Z",
            "valid_until": "2026-09-28T00:00:00Z",
        }
        self.assertValid(self._doc_with_deps(_dependencies(temporal=temporal)))

    def test_missing_dependency_dimension_rejected(self):
        for dimension in ("body", "environment", "frame", "temporal"):
            with self.subTest(dimension=dimension):
                deps = _dependencies()
                del deps[dimension]
                self.assertInvalid(self._doc_with_deps(deps))

    def test_extra_dependency_dimension_rejected(self):
        deps = _dependencies()
        deps["spatial"] = _spatial()
        self.assertInvalid(self._doc_with_deps(deps))

    def test_dependencies_must_be_object(self):
        for bad in ([], "deps", None):
            with self.subTest(dependencies=bad):
                self.assertInvalid(self._doc_with_deps(bad))

    def test_invalid_mode_rejected(self):
        for dimension in ("body", "environment", "frame"):
            for bad in ("INTERVAL", "APPROXIMATE", "none", "", 1, None):
                with self.subTest(dimension=dimension, mode=bad):
                    self.assertInvalid(
                        self._doc_with_deps(_dependencies(**{dimension: _spatial(bad)}))
                    )
        for bad in ("EXACT", "APPROXIMATE", "", 1, None):
            with self.subTest(dimension="temporal", mode=bad):
                self.assertInvalid(self._doc_with_deps(_dependencies(temporal=_spatial(bad))))

    def test_missing_mode_rejected(self):
        self.assertInvalid(self._doc_with_deps(_dependencies(body={"id": "x"})))
        self.assertInvalid(self._doc_with_deps(_dependencies(temporal={})))

    def test_exact_requires_non_empty_id_and_exact_keys(self):
        for bad_id in ("", 1, None):
            with self.subTest(id=bad_id):
                self.assertInvalid(
                    self._doc_with_deps(_dependencies(body=_spatial("EXACT", id=bad_id)))
                )
        self.assertInvalid(self._doc_with_deps(_dependencies(body=_spatial("EXACT"))))
        self.assertInvalid(
            self._doc_with_deps(
                _dependencies(body=_spatial("EXACT", id="mock-uav-A", note="extra"))
            )
        )

    def test_none_and_unknown_must_not_carry_id(self):
        for mode in ("NONE", "UNKNOWN"):
            with self.subTest(mode=mode):
                self.assertInvalid(
                    self._doc_with_deps(_dependencies(frame=_spatial(mode, id="x")))
                )

    def test_dependency_must_be_object(self):
        for bad in ("NONE", [], None):
            with self.subTest(dependency=bad):
                self.assertInvalid(self._doc_with_deps(_dependencies(body=bad)))

    def test_interval_requires_both_bounds_and_exact_keys(self):
        self.assertInvalid(
            self._doc_with_deps(
                _dependencies(temporal={"mode": "INTERVAL", "valid_from": RECORDED_AT})
            )
        )
        self.assertInvalid(
            self._doc_with_deps(
                _dependencies(temporal={"mode": "INTERVAL", "valid_until": RECORDED_AT})
            )
        )
        self.assertInvalid(
            self._doc_with_deps(
                _dependencies(
                    temporal={
                        "mode": "INTERVAL",
                        "valid_from": RECORDED_AT,
                        "valid_until": RECORDED_AT,
                        "note": "extra",
                    }
                )
            )
        )

    def test_interval_bounds_must_be_utc_timestamps(self):
        for bound in ("valid_from", "valid_until"):
            for bad in (
                "not-a-time",
                "2026-09-28T00:00:00+05:00",
                "2026-09-28T00:00:00-00:00",
                "2026-02-30T00:00:00Z",
                None,
                1,
            ):
                with self.subTest(bound=bound, value=bad):
                    temporal = {
                        "mode": "INTERVAL",
                        "valid_from": RECORDED_AT,
                        "valid_until": "2026-09-29T00:00:00Z",
                    }
                    temporal[bound] = bad
                    self.assertInvalid(self._doc_with_deps(_dependencies(temporal=temporal)))

    def test_interval_bounds_accept_plus_utc_offset_form(self):
        temporal = {
            "mode": "INTERVAL",
            "valid_from": "2026-09-28T00:00:00+00:00",
            "valid_until": "2026-09-29T00:00:00.5+00:00",
        }
        self.assertValid(self._doc_with_deps(_dependencies(temporal=temporal)))

    def test_temporal_none_and_unknown_must_have_exact_keys(self):
        for mode in ("NONE", "UNKNOWN"):
            with self.subTest(mode=mode):
                self.assertInvalid(
                    self._doc_with_deps(_dependencies(temporal=_spatial(mode, id="x")))
                )


class TestStructuralSemanticBoundary(ValidationCase):
    """Freeze section 8.2 / invariant 10: the kind-by-dependency authority
    table is P1.2 semantic validation. Global-shape-valid combinations that
    the table will reject MUST pass the P1.1 structural validator."""

    def test_place_body_unknown_is_structurally_valid(self):
        self.assertValid(_document([_place_record(dependencies=_dependencies(body=_spatial("UNKNOWN")))]))

    def test_place_environment_unknown_is_structurally_valid(self):
        self.assertValid(
            _document([_place_record(dependencies=_dependencies(environment=_spatial("UNKNOWN")))])
        )

    def test_place_frame_unknown_is_structurally_valid(self):
        self.assertValid(
            _document([_place_record(dependencies=_dependencies(frame=_spatial("UNKNOWN")))])
        )

    def test_place_environment_none_is_structurally_valid(self):
        # The kind table requires place environment/frame EXACT; NONE is
        # structurally legal and semantically rejected later.
        deps = _dependencies(environment=_spatial("NONE"), frame=_spatial("NONE"))
        self.assertValid(_document([_place_record(dependencies=deps)]))

    def test_body_parameter_with_kind_table_disallowed_modes_is_structurally_valid(self):
        interval = {
            "mode": "INTERVAL",
            "valid_from": "2026-09-28T00:00:00Z",
            "valid_until": "2026-09-29T00:00:00Z",
        }
        disallowed = (
            _dependencies(body=_spatial("NONE")),
            _dependencies(body=_spatial("UNKNOWN")),
            _dependencies(body=_spatial("EXACT", id="b"), environment=_spatial("EXACT", id="e")),
            _dependencies(body=_spatial("EXACT", id="b"), frame=_spatial("UNKNOWN")),
            _dependencies(body=_spatial("EXACT", id="b"), temporal=interval),
            _dependencies(body=_spatial("EXACT", id="b"), temporal=_spatial("UNKNOWN")),
        )
        for deps in disallowed:
            with self.subTest(dependencies=deps):
                self.assertValid(_document([_body_param_record(dependencies=deps)]))


class TestUuids(ValidationCase):
    def test_invalid_experience_id_rejected(self):
        for bad in ("not-a-uuid", _uid().upper(), _uid().replace("-", ""), "", 1, None):
            with self.subTest(experience_id=bad):
                self.assertInvalid(_document([_place_record(experience_id=bad)]))

    def test_invalid_derived_from_item_rejected(self):
        for bad in ("not-a-uuid", _uid().upper(), "", 1, None):
            with self.subTest(item=bad):
                self.assertInvalid(_document([_place_record(derived_from=[bad])]))

    def test_invalid_supersedes_rejected(self):
        for bad in ("not-a-uuid", _uid().upper(), "", 1, [_uid()]):
            with self.subTest(supersedes=bad):
                self.assertInvalid(_document([_place_record(supersedes=bad)]))


class TestTimestamps(ValidationCase):
    def test_recorded_at_accepts_utc_rfc3339(self):
        for good in (
            "2026-09-28T00:00:00Z",
            "2026-09-28T00:00:00+00:00",     # '+00:00' is an equivalent UTC expression
            "2026-09-28T12:30:45.123Z",
            "2026-09-28T12:30:45.123+00:00",
            "1999-01-01T23:59:59Z",
        ):
            with self.subTest(recorded_at=good):
                self.assertValid(_document([_place_record(recorded_at=good)]))

    def test_recorded_at_rejects_malformed_and_non_utc(self):
        bad_values = (
            "2026-09-28",                    # date only
            "2026-09-28T00:00:00",           # missing timezone
            "2026-09-28 00:00:00Z",          # space separator
            "2026-09-28T08:00:00+08:00",     # non-zero offset
            "2026-09-28T00:00:00+05:00",     # non-zero offset
            "2026-09-28T00:00:00-00:00",     # negative-zero offset: unknown, not UTC
            "2026-13-28T00:00:00Z",          # impossible month
            "2026-02-30T00:00:00Z",          # impossible day
            "2026-09-28T25:00:00Z",          # impossible hour
            "not-a-time",
            "",
            1,
            None,
        )
        for bad in bad_values:
            with self.subTest(recorded_at=bad):
                self.assertInvalid(_document([_place_record(recorded_at=bad)]))

    def test_observed_at_accepts_timestamp_or_null(self):
        self.assertValid(_document([_place_record(observed_at=OBSERVED_AT)]))
        self.assertValid(_document([_place_record(observed_at="2026-09-27T12:30:00+00:00")]))
        self.assertValid(_document([_place_record(observed_at=None)]))

    def test_observed_at_rejects_malformed_and_wrong_types(self):
        for bad in (
            "2026-09-28T00:00:00",
            "2026-09-28T00:00:00+02:00",
            "2026-09-28T00:00:00-00:00",
            "2026-02-30T00:00:00Z",
            "x",
            1,
            False,
            [],
        ):
            with self.subTest(observed_at=bad):
                self.assertInvalid(_document([_place_record(observed_at=bad)]))


class TestRelations(ValidationCase):
    def test_derived_from_accepts_zero_one_many_ids(self):
        self.assertValid(_document([_place_record(derived_from=[])]))
        self.assertValid(_document([_place_record(derived_from=[_uid()])]))
        self.assertValid(_document([_place_record(derived_from=[_uid(), _uid(), _uid()])]))

    def test_derived_from_must_be_array(self):
        for bad in ({}, "x", None, 1):
            with self.subTest(derived_from=bad):
                self.assertInvalid(_document([_place_record(derived_from=bad)]))

    def test_derived_from_items_must_be_uuid_strings(self):
        self.assertInvalid(_document([_place_record(derived_from=[123])]))
        self.assertInvalid(_document([_place_record(derived_from=[None])]))

    def test_supersedes_accepts_null_or_canonical_uuid(self):
        self.assertValid(_document([_place_record(supersedes=None)]))
        self.assertValid(_document([_place_record(supersedes=_uid())]))

    def test_supersedes_wrong_type_rejected(self):
        for bad in (1, [], {}, True):
            with self.subTest(supersedes=bad):
                self.assertInvalid(_document([_place_record(supersedes=bad)]))


class TestNonStringKeys(ValidationCase):
    """A Python dict carrying non-string keys is not a legal JSON object
    representation. It must fail closed with INVALID_EXPERIENCE and must
    never leak TypeError/ValueError/KeyError from diagnostics."""

    def assert_fails_closed(self, document):
        with self.assertRaises(ExperienceValidationError) as ctx:
            validate_experience_document_structure(document)
        self.assertEqual(ctx.exception.code, ec.INVALID_EXPERIENCE)

    def test_document_level_non_string_key(self):
        doc = _document()
        doc[123] = "bad"
        self.assert_fails_closed(doc)

    def test_document_level_mixed_heterogeneous_keys(self):
        doc = _document()
        doc[123] = "bad"
        doc[4.5] = "bad"
        doc[("tuple",)] = "bad"
        self.assert_fails_closed(doc)

    def test_record_level_non_string_key(self):
        record = _place_record()
        record[42] = "bad"
        self.assert_fails_closed(_document([record]))

    def test_payload_level_non_string_key(self):
        record = _place_record()
        record["payload"][7] = "bad"
        self.assert_fails_closed(_document([record]))

    def test_position_level_non_string_key(self):
        record = _place_record()
        record["payload"]["position"][0] = "bad"
        self.assert_fails_closed(_document([record]))

    def test_provenance_level_non_string_key(self):
        record = _place_record()
        record["provenance"][1] = "x"
        self.assert_fails_closed(_document([record]))

    def test_dependency_level_non_string_key(self):
        record = _place_record()
        record["dependencies"]["body"][9] = "bad"
        self.assert_fails_closed(_document([record]))

    def test_keys_are_not_normalized(self):
        # Non-string keys are rejected, never coerced to strings.
        doc = _document()
        doc[123] = "bad"
        self.assert_fails_closed(doc)
        self.assertNotIn("123", doc)


class TestValidatorBehavior(ValidationCase):
    def test_input_is_not_mutated(self):
        document = _document(
            [
                _place_record(
                    dependencies=_dependencies(
                        body=_spatial("EXACT", id="mock-uav-A"),
                        temporal={
                            "mode": "INTERVAL",
                            "valid_from": RECORDED_AT,
                            "valid_until": "2026-09-29T00:00:00Z",
                        },
                    ),
                    derived_from=[_uid()],
                )
            ]
        )
        snapshot = copy.deepcopy(document)
        validate_experience_document_structure(document)
        self.assertEqual(document, snapshot)

    def test_failure_leaves_no_partial_state(self):
        # Validating an invalid document must not make a later valid one fail.
        with self.assertRaises(ExperienceValidationError):
            validate_experience_document_structure(_document([_place_record(kind="bogus")]))
        self.assertValid(_document([_place_record()]))


class TestSharedConformanceFixtures(ValidationCase):
    """Shared positive/negative fixtures (Freeze section 10.1) make the
    structural acceptance set auditable against the normative JSON Schema
    with any external Draft 2020-12 tool; the Runtime validator must accept
    all positive fixtures and reject all negative fixtures."""

    def test_fixtures_exist(self):
        self.assertTrue(any((FIXTURES / "positive").glob("*.json")))
        self.assertTrue(any((FIXTURES / "negative").glob("*.json")))

    def test_positive_fixtures_are_structurally_valid(self):
        for path in sorted((FIXTURES / "positive").glob("*.json")):
            with self.subTest(fixture=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertValid(value)

    def test_negative_fixtures_are_rejected(self):
        for path in sorted((FIXTURES / "negative").glob("*.json")):
            with self.subTest(fixture=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                with self.assertRaises(ExperienceValidationError):
                    validate_experience_document_structure(value)


class TestNormativeSchema(unittest.TestCase):
    def test_schema_is_draft_2020_12_and_self_contained(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

        refs = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "$ref":
                        refs.append(value)
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(schema)
        self.assertTrue(refs)
        for ref in refs:
            self.assertTrue(ref.startswith("#/"), f"remote $ref found: {ref}")

    def test_schema_declares_exact_key_document(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["additionalProperties"], False)
        self.assertEqual(
            set(schema["required"]), {"schema_version", "agent_id", "records"}
        )

    def test_timestamp_definition_keeps_calendar_validity(self):
        # Regression guard: utcTimestamp must pair the bounded UTC lexical
        # pattern with "format": "date-time" so impossible calendar values
        # are rejected when format assertion is enabled, matching the
        # Runtime validator's datetime.fromisoformat check.
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        timestamp = schema["$defs"]["utcTimestamp"]
        self.assertEqual(timestamp["format"], "date-time")
        self.assertIn("pattern", timestamp)
        self.assertIn("Z", timestamp["pattern"])
        self.assertIn("+00:00", timestamp["pattern"])

    def test_schema_version_definition_matches_numeric_equality(self):
        # Regression guard: const 1 accepts the JSON numbers 1 and 1.0
        # (JSON Schema numeric equality) while bool is excluded, matching
        # the Runtime validator.
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        version = schema["properties"]["schema_version"]
        self.assertEqual(version["const"], 1)
        self.assertEqual(version["not"], {"type": "boolean"})


class TestNormativeSchemaConformance(ValidationCase):
    """External Draft 2020-12 conformance evidence (Freeze section 10.1).

    Dev-only: runs the normative schema against every shared fixture with
    format assertion enabled and asserts schema/runtime agreement. Skipped
    when the optional 'jsonschema' package (with an active date-time format
    checker, e.g. rfc3339-validator) is not installed; the Phanes Runtime
    itself never depends on it.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from jsonschema import Draft202012Validator, FormatChecker
        except ImportError:
            raise unittest.SkipTest(
                "jsonschema not installed; dev-only schema conformance skipped"
            )
        format_checker = FormatChecker()
        if "date-time" not in format_checker.checkers:
            raise unittest.SkipTest(
                "no active date-time format checker (install rfc3339-validator)"
            )
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        cls._validator = Draft202012Validator(schema, format_checker=format_checker)

    def test_positive_fixtures_agree(self):
        for path in sorted((FIXTURES / "positive").glob("*.json")):
            with self.subTest(fixture=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(self._validator.is_valid(value), "schema rejected")
                self.assertValid(value)

    def test_negative_fixtures_agree(self):
        for path in sorted((FIXTURES / "negative").glob("*.json")):
            with self.subTest(fixture=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertFalse(self._validator.is_valid(value), "schema accepted")
                with self.assertRaises(ExperienceValidationError):
                    validate_experience_document_structure(value)


if __name__ == "__main__":
    unittest.main()
