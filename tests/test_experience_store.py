"""P1.2 ExperienceStore persistence and immutable lifecycle tests."""

import copy
import inspect
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phanes.experience_contracts import (
    DUPLICATE_EXPERIENCE_ID,
    EXPERIENCE_NOT_FOUND,
    INVALID_EXPERIENCE,
    UNSUPPORTED_SCHEMA_VERSION,
)
from phanes.experience_store import EXPERIENCE_FILENAME, ExperienceStore, ExperienceStoreError
from phanes.experience_validation import ExperienceValidationError
from phanes.identity import IdentityError, init_identity
from test_experience_semantics import record


class StoreCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "self"
        self.identity = init_identity(self.state)
        self.path = self.state / EXPERIENCE_FILENAME

    def assert_code(self, code, operation):
        with self.assertRaises(ExperienceValidationError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")


class TestCreateAndLoad(StoreCase):
    def test_create_exact_empty_document_and_reload(self):
        store = ExperienceStore.create(self.state, self.identity.agent_id)
        expected = {"schema_version": 1, "agent_id": self.identity.agent_id, "records": []}
        self.assertEqual(self.read(), expected)
        self.assertIs(type(self.read()["schema_version"]), int)
        self.assertEqual(store.document_snapshot(), expected)
        self.assertEqual(ExperienceStore.load(self.state, self.identity.agent_id).document_snapshot(), expected)

    def test_create_refuses_overwrite(self):
        ExperienceStore.create(self.state, self.identity.agent_id)
        before = self.path.read_bytes()
        with self.assertRaises(ExperienceStoreError):
            ExperienceStore.create(self.state, self.identity.agent_id)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_and_corrupt_file_fail_closed(self):
        with self.assertRaises(ExperienceStoreError):
            ExperienceStore.load(self.state, self.identity.agent_id)
        self.assertFalse(self.path.exists())
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ExperienceStoreError):
            ExperienceStore.load(self.state, self.identity.agent_id)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_identity_arguments_must_match_existing_identity(self):
        other = "04ebb5c8-826d-4335-9c77-a31140c3499e"
        self.assert_code(INVALID_EXPERIENCE, lambda: ExperienceStore.create(self.state, other))
        self.assertFalse(self.path.exists())
        ExperienceStore.create(self.state, self.identity.agent_id)
        self.assert_code(INVALID_EXPERIENCE, lambda: ExperienceStore.load(self.state, other))

    def test_load_rejects_structural_version_and_semantic_corruption(self):
        ExperienceStore.create(self.state, self.identity.agent_id)
        raw = self.read()
        self.write(dict(raw, schema_version=2))
        self.assert_code(UNSUPPORTED_SCHEMA_VERSION, lambda: ExperienceStore.load(self.state, self.identity.agent_id))
        self.write(dict(raw, extra=True))
        self.assert_code(INVALID_EXPERIENCE, lambda: ExperienceStore.load(self.state, self.identity.agent_id))
        self.write(dict(raw, agent_id="04ebb5c8-826d-4335-9c77-a31140c3499e"))
        self.assert_code(INVALID_EXPERIENCE, lambda: ExperienceStore.load(self.state, self.identity.agent_id))
        bad = record(agent_id=self.identity.agent_id)
        bad["dependencies"]["environment"] = {"mode": "UNKNOWN"}
        self.write(dict(raw, records=[bad]))
        self.assert_code(INVALID_EXPERIENCE, lambda: ExperienceStore.load(self.state, self.identity.agent_id))

    def test_direct_constructor_cannot_bypass_validation_or_identity(self):
        ExperienceStore.create(self.state, self.identity.agent_id)
        invalid = {"schema_version": 1, "agent_id": self.identity.agent_id, "records": [record(agent_id=self.identity.agent_id)]}
        invalid["records"][0]["dependencies"]["frame"] = {"mode": "NONE"}
        self.write(invalid)
        self.assert_code(INVALID_EXPERIENCE, lambda: ExperienceStore(self.state, self.identity.agent_id))
        self.assert_code(INVALID_EXPERIENCE, lambda: ExperienceStore(self.state, "04ebb5c8-826d-4335-9c77-a31140c3499e"))

    def test_public_api_has_no_arbitrary_path_document_or_whole_document_save(self):
        self.assertEqual(list(inspect.signature(ExperienceStore).parameters), ["state_dir", "identity_agent_id"])
        self.assertEqual(list(inspect.signature(ExperienceStore.create).parameters), ["state_dir", "agent_id"])
        self.assertEqual(list(inspect.signature(ExperienceStore.load).parameters), ["state_dir", "identity_agent_id"])
        for name in ("save", "save_document", "save_experiences", "replace", "replace_document", "replace_record"):
            self.assertFalse(hasattr(ExperienceStore, name))

    def test_constructor_cannot_replace_existing_history_with_supplied_empty_document(self):
        store = ExperienceStore.create(self.state, self.identity.agent_id)
        first = record(agent_id=self.identity.agent_id)
        store.append_record(first)
        empty = {"schema_version": 1, "agent_id": self.identity.agent_id, "records": []}
        with self.assertRaises(TypeError):
            ExperienceStore(self.path, self.identity.agent_id, empty)
        second = record(agent_id=self.identity.agent_id)
        ExperienceStore(self.state, self.identity.agent_id).append_record(second)
        self.assertEqual(self.read()["records"], [first, second])
        self.assertEqual(ExperienceStore.load(self.state, self.identity.agent_id).document_snapshot()["records"], [first, second])

    def test_constructor_cannot_target_non_experience_files(self):
        ExperienceStore.create(self.state, self.identity.agent_id)
        identity_path = self.state / "identity.json"
        memory_path = self.state / "memory.json"
        memory_path.write_text("memory sentinel", encoding="utf-8")
        before_identity = identity_path.read_bytes()
        before_memory = memory_path.read_bytes()
        before_experience = self.path.read_bytes()
        empty = {"schema_version": 1, "agent_id": self.identity.agent_id, "records": []}
        for filename in ("unexpected.json", "identity.json", "memory.json"):
            target = self.state / filename
            with self.subTest(filename=filename):
                with self.assertRaises(TypeError):
                    ExperienceStore(target, self.identity.agent_id, empty)
                with self.assertRaises(IdentityError):
                    ExperienceStore(target, self.identity.agent_id)
                self.assertEqual(identity_path.read_bytes(), before_identity)
                self.assertEqual(memory_path.read_bytes(), before_memory)
                self.assertEqual(self.path.read_bytes(), before_experience)
                self.assertFalse((self.state / "unexpected.json").exists())


class TestAppendAndRead(StoreCase):
    def setUp(self):
        super().setUp()
        self.store = ExperienceStore.create(self.state, self.identity.agent_id)

    def item(self):
        return record(agent_id=self.identity.agent_id)

    def test_append_and_exact_id_lookup_survive_restart(self):
        item = self.item()
        self.store.append_record(item)
        self.assertEqual(self.read()["records"], [item])
        self.assertEqual(self.store.get_record(item["experience_id"]), item)
        reloaded = ExperienceStore.load(self.state, self.identity.agent_id)
        self.assertEqual(reloaded.get_record(item["experience_id"]), item)
        self.assert_code(EXPERIENCE_NOT_FOUND, lambda: reloaded.get_record("dock-a"))
        self.assert_code(EXPERIENCE_NOT_FOUND, lambda: reloaded.get_record("missing"))
        self.assertFalse(hasattr(self.store, "lookup_place"))
        self.assertFalse(hasattr(self.store, "latest"))

    def test_duplicate_and_invalid_append_preserve_disk_and_memory(self):
        item = self.item()
        self.store.append_record(item)
        before_disk = self.path.read_bytes()
        before_memory = self.store.document_snapshot()
        self.assert_code(DUPLICATE_EXPERIENCE_ID, lambda: self.store.append_record(copy.deepcopy(item)))
        invalid = self.item()
        invalid["dependencies"]["frame"] = {"mode": "NONE"}
        self.assert_code(INVALID_EXPERIENCE, lambda: self.store.append_record(invalid))
        self.assertEqual(self.path.read_bytes(), before_disk)
        self.assertEqual(self.store.document_snapshot(), before_memory)

    def test_write_failure_preserves_state_cleans_temp_and_allows_retry(self):
        first = self.item()
        self.store.append_record(first)
        second = self.item()
        before_disk = self.path.read_bytes()
        before_memory = self.store.document_snapshot()
        with patch("os.replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.store.append_record(second)
        self.assertEqual(self.path.read_bytes(), before_disk)
        self.assertEqual(self.store.document_snapshot(), before_memory)
        self.assertFalse(any(path.name.endswith(".tmp") for path in self.state.iterdir()))
        self.store.append_record(second)
        self.assertEqual(len(ExperienceStore.load(self.state, self.identity.agent_id).document_snapshot()["records"]), 2)

    def test_caller_and_returned_values_are_defensive_copies(self):
        item = self.item()
        original = copy.deepcopy(item)
        self.store.append_record(item)
        item["payload"]["position"]["x"] = 999
        snapshot = self.store.document_snapshot()
        snapshot["records"][0]["payload"]["position"]["x"] = 888
        fetched = self.store.get_record(original["experience_id"])
        fetched["payload"]["position"]["x"] = 777
        self.assertEqual(self.store.get_record(original["experience_id"]), original)
        self.assertEqual(self.read()["records"][0], original)

    def test_correction_retains_old_record_and_never_follows_successor(self):
        old = self.item()
        self.store.append_record(old)
        correction = self.item()
        correction["payload"]["position"] = {"x": 30, "y": 40}
        correction["supersedes"] = old["experience_id"]
        self.store.append_record(correction)
        self.assertEqual(self.store.get_record(old["experience_id"]), old)
        self.assertEqual(self.store.get_record(correction["experience_id"]), correction)
        self.assertEqual(ExperienceStore.load(self.state, self.identity.agent_id).document_snapshot()["records"], [old, correction])
        for method in ("update_record", "replace_record", "delete_record", "save_experiences"):
            self.assertFalse(hasattr(self.store, method))

    def test_create_failure_does_not_leave_file(self):
        # Test a separate directory because setUp has already created the store.
        with TemporaryDirectory() as temporary:
            state = Path(temporary) / "self"
            identity = init_identity(state)
            with patch("os.replace", side_effect=OSError("simulated disk failure")):
                with self.assertRaises(OSError):
                    ExperienceStore.create(state, identity.agent_id)
            self.assertFalse((state / EXPERIENCE_FILENAME).exists())
            self.assertFalse(any(path.name.endswith(".tmp") for path in state.iterdir()))


if __name__ == "__main__":
    unittest.main()
