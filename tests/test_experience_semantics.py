"""P1.2 semantic validation and its P1.1 boundary."""

import ast
import copy
import sys
import unittest
import uuid
from pathlib import Path

from phanes.experience_contracts import (
    BROKEN_EXPERIENCE_REFERENCE,
    DUPLICATE_EXPERIENCE_ID,
    EXPERIENCE_REFERENCE_CYCLE,
    INVALID_EXPERIENCE,
    SUPERSESSION_SUBJECT_MISMATCH,
)
from phanes.experience_semantic import validate_experience_document
from phanes.experience_validation import (
    ExperienceValidationError,
    validate_experience_document_structure,
)

AGENT_ID = "7d4d40b9-9f8a-40b1-80ec-cb0333bdb2b7"
OTHER_ID = "04ebb5c8-826d-4335-9c77-a31140c3499e"


def record(kind="place_observation", **changes):
    if kind == "place_observation":
        payload = {"place_id": "dock-a", "position": {"x": 10, "y": 20}}
        body = {"mode": "NONE"}
        environment = {"mode": "EXACT", "id": "env-a"}
        frame = {"mode": "EXACT", "id": "frame-a"}
    else:
        payload = {"parameter_name": "ROLL_KP", "value": 0.18, "unit": "mock-unit"}
        body = {"mode": "EXACT", "id": "body-a"}
        environment = {"mode": "NONE"}
        frame = {"mode": "NONE"}
    value = {
        "experience_id": str(uuid.uuid4()),
        "agent_id": AGENT_ID,
        "kind": kind,
        "payload": payload,
        "provenance": {"source_class": "test_fixture", "source_ref": "test:1"},
        "dependencies": {
            "body": body,
            "environment": environment,
            "frame": frame,
            "temporal": {"mode": "NONE"},
        },
        "recorded_at": "2026-09-28T00:00:00Z",
        "observed_at": None,
        "derived_from": [],
        "supersedes": None,
    }
    value.update(changes)
    return value


def document(*records, agent_id=AGENT_ID):
    return {"schema_version": 1, "agent_id": agent_id, "records": list(records)}


class SemanticCase(unittest.TestCase):
    def assert_valid(self, value):
        validate_experience_document(value, AGENT_ID)

    def assert_error(self, value, code):
        with self.assertRaises(ExperienceValidationError) as caught:
            validate_experience_document(value, AGENT_ID)
        self.assertEqual(caught.exception.code, code)


class TestAgentAndIds(SemanticCase):
    def test_empty_and_two_independent_same_subject(self):
        self.assert_valid(document())
        self.assert_valid(document(record(), record()))

    def test_document_and_record_agent_mismatch(self):
        self.assert_error(document(agent_id=OTHER_ID), INVALID_EXPERIENCE)
        self.assert_error(document(record(agent_id=OTHER_ID)), INVALID_EXPERIENCE)

    def test_duplicate_id_before_index_overwrite(self):
        parent = record()
        duplicate = record(experience_id=parent["experience_id"])
        self.assert_error(document(parent, duplicate), DUPLICATE_EXPERIENCE_ID)

    def test_structural_errors_precede_semantics(self):
        value = document(record(agent_id=OTHER_ID))
        value["records"][0]["kind"] = "future_kind"
        self.assert_error(value, INVALID_EXPERIENCE)


class TestKindDependencies(SemanticCase):
    def test_place_allowed_modes(self):
        for body in ({"mode": "NONE"}, {"mode": "EXACT", "id": "body-a"}):
            for temporal in (
                {"mode": "NONE"},
                {"mode": "UNKNOWN"},
                {"mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z", "valid_until": "2026-09-29T00:00:00Z"},
            ):
                with self.subTest(body=body, temporal=temporal):
                    item = record()
                    item["dependencies"]["body"] = body
                    item["dependencies"]["temporal"] = temporal
                    self.assert_valid(document(item))

    def test_parameter_allowed_row(self):
        self.assert_valid(document(record("body_parameter_observation")))

    def test_place_disallowed_modes_pass_structure_only(self):
        invalid = (
            ("body", {"mode": "UNKNOWN"}),
            ("environment", {"mode": "NONE"}),
            ("environment", {"mode": "UNKNOWN"}),
            ("frame", {"mode": "NONE"}),
            ("frame", {"mode": "UNKNOWN"}),
        )
        for dimension, dependency in invalid:
            with self.subTest(dimension=dimension, dependency=dependency):
                item = record()
                item["dependencies"][dimension] = dependency
                value = document(item)
                validate_experience_document_structure(value)
                self.assert_error(value, INVALID_EXPERIENCE)

    def test_parameter_disallowed_modes_pass_structure_only(self):
        invalid = (
            ("body", {"mode": "NONE"}),
            ("body", {"mode": "UNKNOWN"}),
            ("environment", {"mode": "EXACT", "id": "env"}),
            ("environment", {"mode": "UNKNOWN"}),
            ("frame", {"mode": "EXACT", "id": "frame"}),
            ("frame", {"mode": "UNKNOWN"}),
            ("temporal", {"mode": "INTERVAL", "valid_from": "2026-09-28T00:00:00Z", "valid_until": "2026-09-29T00:00:00Z"}),
            ("temporal", {"mode": "UNKNOWN"}),
        )
        for dimension, dependency in invalid:
            with self.subTest(dimension=dimension, dependency=dependency):
                item = record("body_parameter_observation")
                item["dependencies"][dimension] = dependency
                value = document(item)
                validate_experience_document_structure(value)
                self.assert_error(value, INVALID_EXPERIENCE)


class TestTemporal(SemanticCase):
    def test_ordering_accepts_z_offset_and_fraction(self):
        for bounds in (
            ("2026-09-28T00:00:00Z", "2026-09-29T00:00:00Z"),
            ("2026-09-28T00:00:00+00:00", "2026-09-29T00:00:00+00:00"),
            ("2026-09-28T00:00:00.1Z", "2026-09-28T00:00:00.2+00:00"),
            ("2026-09-28T00:00:00.0000001Z", "2026-09-28T00:00:00.0000002+00:00"),
        ):
            item = record()
            item["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": bounds[0], "valid_until": bounds[1]}
            self.assert_valid(document(item))

    def test_equal_and_reversed_are_semantic_only(self):
        for start, end in (
            ("2026-09-28T00:00:00Z", "2026-09-28T00:00:00+00:00"),
            ("2026-09-29T00:00:00Z", "2026-09-28T00:00:00Z"),
            ("2026-09-28T00:00:00.1000000Z", "2026-09-28T00:00:00.1+00:00"),
        ):
            item = record()
            item["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": start, "valid_until": end}
            value = document(item)
            validate_experience_document_structure(value)
            self.assert_error(value, INVALID_EXPERIENCE)

    def test_no_recorded_observed_or_current_time_check(self):
        item = record(recorded_at="2026-09-30T00:00:00Z", observed_at="2026-09-01T00:00:00Z")
        item["dependencies"]["temporal"] = {"mode": "INTERVAL", "valid_from": "2026-09-10T00:00:00Z", "valid_until": "2026-09-11T00:00:00Z"}
        self.assert_valid(document(item))


class TestRelations(SemanticCase):
    def test_derived_from_zero_one_many_and_dag(self):
        a, b, c, d = record(), record(), record(), record()
        c["derived_from"] = [a["experience_id"], b["experience_id"]]
        d["derived_from"] = [c["experience_id"], a["experience_id"]]
        self.assert_valid(document(a, b, c, d))

    def test_missing_and_duplicate_derived_parent(self):
        child = record(derived_from=[str(uuid.uuid4())])
        self.assert_error(document(child), BROKEN_EXPERIENCE_REFERENCE)
        parent = record()
        child["derived_from"] = [parent["experience_id"], parent["experience_id"]]
        self.assert_error(document(parent, child), INVALID_EXPERIENCE)

    def test_derived_self_two_node_and_long_cycle(self):
        for size in (1, 2, 4):
            items = [record() for _ in range(size)]
            for index, item in enumerate(items):
                item["derived_from"] = [items[(index + 1) % size]["experience_id"]]
            value = document(*items)
            validate_experience_document_structure(value)
            self.assert_error(value, EXPERIENCE_REFERENCE_CYCLE)

    def test_supersedes_missing_self_and_cycles(self):
        self.assert_error(document(record(supersedes=str(uuid.uuid4()))), BROKEN_EXPERIENCE_REFERENCE)
        for size in (1, 2, 4):
            items = [record() for _ in range(size)]
            for index, item in enumerate(items):
                item["supersedes"] = items[(index + 1) % size]["experience_id"]
            self.assert_error(document(*items), EXPERIENCE_REFERENCE_CYCLE)

    def test_supersession_chain_and_branch(self):
        a, b, c = record(), record(), record()
        b["supersedes"] = a["experience_id"]
        c["supersedes"] = b["experience_id"]
        self.assert_valid(document(a, b, c))
        c["supersedes"] = a["experience_id"]
        self.assert_error(document(a, b, c), INVALID_EXPERIENCE)

    def test_place_subject_and_kind_mismatch(self):
        old = record()
        correction = record(supersedes=old["experience_id"])
        self.assert_valid(document(old, correction))
        correction["payload"]["place_id"] = " dock-a "
        self.assert_error(document(old, correction), SUPERSESSION_SUBJECT_MISMATCH)
        parameter = record("body_parameter_observation", supersedes=old["experience_id"])
        self.assert_error(document(old, parameter), SUPERSESSION_SUBJECT_MISMATCH)

    def test_parameter_subject_components(self):
        old = record("body_parameter_observation")
        correction = record("body_parameter_observation", supersedes=old["experience_id"])
        self.assert_valid(document(old, correction))
        for field, value in (("body", "body-b"), ("parameter_name", "PITCH_KP"), ("unit", "other-unit")):
            changed = copy.deepcopy(correction)
            if field == "body":
                changed["dependencies"]["body"]["id"] = value
            else:
                changed["payload"][field] = value
            self.assert_error(document(old, changed), SUPERSESSION_SUBJECT_MISMATCH)

    def test_mixed_graph_valid_and_cycle(self):
        a, b, c = record(), record(), record()
        b["supersedes"] = a["experience_id"]
        c["derived_from"] = [b["experience_id"]]
        self.assert_valid(document(a, b, c))
        a["derived_from"] = [c["experience_id"]]
        self.assert_error(document(a, b, c), EXPERIENCE_REFERENCE_CYCLE)

    def test_no_mutation_and_repeatability(self):
        parent = record()
        child = record(derived_from=[parent["experience_id"]])
        value = document(parent, child)
        before = copy.deepcopy(value)
        self.assert_valid(value)
        self.assert_valid(value)
        self.assertEqual(value, before)

    def test_deep_chain_avoids_recursion_limit(self):
        items = [record() for _ in range(1100)]
        for index in range(1, len(items)):
            items[index]["derived_from"] = [items[index - 1]["experience_id"]]
        self.assert_valid(document(*items))


class TestBoundary(unittest.TestCase):
    def test_runtime_imports_only_stdlib_and_phanes(self):
        root = Path(__file__).resolve().parent.parent
        for name in ("experience_semantic.py", "experience_store.py"):
            tree = ast.parse((root / "phanes" / name).read_text(encoding="utf-8"))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports.add(node.module.split(".")[0])
            self.assertEqual(imports - set(sys.stdlib_module_names) - {"phanes"}, set())
            self.assertNotIn("phanes_host", imports)


if __name__ == "__main__":
    unittest.main()
