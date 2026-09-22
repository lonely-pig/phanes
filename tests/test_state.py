"""P0.2 state tests: Identity and Memory persistence (Freeze section 14).

Covers AT01 (init/restart), AT02 (memory persistence), AT16 (write failure)
and the Self-related subcases of AT10/AT11 (strict validation, corruption).
"""

import json
import unittest
import unittest.mock
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from phanes.contracts import Position, is_valid_coordinate
from phanes.identity import IDENTITY_FILENAME, IdentityError, init_identity, load_identity
from phanes.memory import MEMORY_FILENAME, MemoryStore, MemoryStoreError


class StateDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "state"

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, path: Path, payload) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")


class TestIdentity(StateDirTestCase):
    def test_init_creates_valid_identity(self):
        ident = init_identity(self.state)
        uuid.UUID(ident.agent_id)  # parses as UUID
        self.assertEqual(ident.name, "Phanes")
        self.assertEqual(ident.schema_version, 1)

        raw = self.read_json(self.state / IDENTITY_FILENAME)
        self.assertEqual(raw["schema_version"], 1)
        self.assertEqual(raw["agent_id"], ident.agent_id)
        self.assertEqual(raw["name"], "Phanes")
        self.assertEqual(raw["created_at"], ident.created_at)
        self.assertTrue(ident.created_at.endswith("Z"))

    def test_agent_id_stable_across_reloads(self):
        ident = init_identity(self.state)
        for _ in range(3):
            loaded = load_identity(self.state)
            self.assertEqual(loaded, ident)

    def test_double_init_rejected_and_state_unchanged(self):
        ident = init_identity(self.state)
        before = (self.state / IDENTITY_FILENAME).read_bytes()
        with self.assertRaises(IdentityError):
            init_identity(self.state)
        self.assertEqual((self.state / IDENTITY_FILENAME).read_bytes(), before)
        self.assertEqual(load_identity(self.state), ident)

    def test_init_rejects_non_directory_path(self):
        file_path = Path(self._tmp.name) / "afile"
        file_path.write_text("x", encoding="utf-8")
        with self.assertRaises(IdentityError):
            init_identity(file_path)

    def test_load_missing_file_fails_without_regeneration(self):
        self.state.mkdir(parents=True)
        with self.assertRaises(IdentityError):
            load_identity(self.state)
        self.assertEqual(list(self.state.iterdir()), [])  # nothing created

    def test_load_corrupt_json_fails_without_regeneration(self):
        init_identity(self.state)
        path = self.state / IDENTITY_FILENAME
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(IdentityError):
            load_identity(self.state)
        self.assertEqual(path.read_text(encoding="utf-8"), "{ not json")  # untouched
        self.assertEqual([p.name for p in self.state.iterdir()], [IDENTITY_FILENAME])

    def test_load_rejects_missing_and_extra_fields(self):
        ident = init_identity(self.state)
        raw = self.read_json(self.state / IDENTITY_FILENAME)

        for key in raw:
            broken = {k: v for k, v in raw.items() if k != key}
            self.write_json(self.state / IDENTITY_FILENAME, broken)
            with self.assertRaises(IdentityError, msg=f"missing {key}"):
                load_identity(self.state)

        extra = dict(raw, unexpected="field")
        self.write_json(self.state / IDENTITY_FILENAME, extra)
        with self.assertRaises(IdentityError):
            load_identity(self.state)

        self.write_json(self.state / IDENTITY_FILENAME, raw)
        self.assertEqual(load_identity(self.state), ident)

    def test_load_rejects_wrong_schema_version(self):
        init_identity(self.state)
        for bad_version in (2, 0, "1", True, None):
            raw = self.read_json(self.state / IDENTITY_FILENAME)
            raw["schema_version"] = bad_version
            self.write_json(self.state / IDENTITY_FILENAME, raw)
            with self.assertRaises(IdentityError, msg=repr(bad_version)):
                load_identity(self.state)

    def test_load_rejects_invalid_field_types(self):
        init_identity(self.state)
        raw = self.read_json(self.state / IDENTITY_FILENAME)
        for field, bad in (
            ("agent_id", "not-a-uuid"),
            ("agent_id", 123),
            ("name", ""),
            ("name", None),
            ("created_at", "not-a-date"),
            ("created_at", 0),
        ):
            broken = dict(raw, **{field: bad})
            self.write_json(self.state / IDENTITY_FILENAME, broken)
            with self.assertRaises(IdentityError, msg=f"{field}={bad!r}"):
                load_identity(self.state)

    def test_load_rejects_non_object_document(self):
        init_identity(self.state)
        self.write_json(self.state / IDENTITY_FILENAME, ["not", "an", "object"])
        with self.assertRaises(IdentityError):
            load_identity(self.state)


class TestMemory(StateDirTestCase):
    def setUp(self):
        super().setUp()
        self.ident = init_identity(self.state)
        self.store = MemoryStore.create(self.state, self.ident.agent_id)

    def reload(self) -> MemoryStore:
        return MemoryStore.load(self.state, self.ident.agent_id)

    def test_create_writes_empty_memory_with_identity_agent_id(self):
        raw = self.read_json(self.state / MEMORY_FILENAME)
        self.assertEqual(raw, {"schema_version": 1, "agent_id": self.ident.agent_id, "places": {}})
        self.assertIsNone(self.store.lookup_place("Alpha"))

    def test_remember_lookup_and_restart_recovery(self):
        self.store.remember_place("Alpha", 10, 20)
        self.assertEqual(self.store.lookup_place("Alpha"), Position(x=10, y=20))

        reloaded = self.reload()
        self.assertEqual(reloaded.lookup_place("Alpha"), Position(x=10, y=20))

    def test_same_name_update_persists(self):
        self.store.remember_place("Alpha", 10, 20)
        self.store.remember_place("Alpha", 1, 2)
        self.assertEqual(self.reload().lookup_place("Alpha"), Position(x=1, y=2))

    def test_negative_and_decimal_coordinates(self):
        self.store.remember_place("Beta", -3.5, 0.25)
        self.assertEqual(self.reload().lookup_place("Beta"), Position(x=-3.5, y=0.25))

    def test_place_name_whitespace_normalized(self):
        self.store.remember_place("  Alpha  ", 10, 20)
        self.assertEqual(self.reload().lookup_place("Alpha"), Position(x=10, y=20))
        self.assertEqual(self.store.lookup_place(" Alpha "), Position(x=10, y=20))

    def test_stored_data_satisfies_coordinate_contract(self):
        self.store.remember_place("Alpha", 10, 20)
        raw = self.read_json(self.state / MEMORY_FILENAME)
        entry = raw["places"]["Alpha"]
        self.assertEqual(set(entry), {"x", "y"})
        self.assertTrue(is_valid_coordinate(entry["x"]))
        self.assertTrue(is_valid_coordinate(entry["y"]))

    def test_remember_rejects_invalid_coordinates(self):
        for bad in (True, False, float("nan"), float("inf"), float("-inf"), "10", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.store.remember_place("Gamma", bad, 20)
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.store.remember_place("Gamma", 10, bad)
        self.assertIsNone(self.store.lookup_place("Gamma"))
        self.assertEqual(self.read_json(self.state / MEMORY_FILENAME)["places"], {})

    def test_remember_rejects_invalid_names(self):
        for bad in ("", "   ", None, 42):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.store.remember_place(bad, 1, 2)

    def test_create_refuses_existing_memory_file(self):
        with self.assertRaises(MemoryStoreError):
            MemoryStore.create(self.state, self.ident.agent_id)

    def test_load_missing_file_fails(self):
        (self.state / MEMORY_FILENAME).unlink()
        with self.assertRaises(MemoryStoreError):
            self.reload()

    def test_load_corrupt_json_fails_not_treated_as_empty(self):
        self.store.remember_place("Alpha", 10, 20)
        (self.state / MEMORY_FILENAME).write_text("{ broken", encoding="utf-8")
        with self.assertRaises(MemoryStoreError):
            self.reload()

    def test_load_rejects_wrong_schema_version(self):
        for bad_version in (2, 0, "1", True, None):
            raw = self.read_json(self.state / MEMORY_FILENAME)
            raw["schema_version"] = bad_version
            self.write_json(self.state / MEMORY_FILENAME, raw)
            with self.assertRaises(MemoryStoreError, msg=repr(bad_version)):
                self.reload()

    def test_load_rejects_agent_id_mismatch(self):
        other = str(uuid.uuid4())
        with self.assertRaises(MemoryStoreError):
            MemoryStore.load(self.state, other)

    def test_load_rejects_missing_and_extra_fields(self):
        raw = self.read_json(self.state / MEMORY_FILENAME)
        for key in raw:
            broken = {k: v for k, v in raw.items() if k != key}
            self.write_json(self.state / MEMORY_FILENAME, broken)
            with self.assertRaises(MemoryStoreError, msg=f"missing {key}"):
                self.reload()

        self.write_json(self.state / MEMORY_FILENAME, dict(raw, extra=1))
        with self.assertRaises(MemoryStoreError):
            self.reload()

    def test_load_rejects_malformed_places(self):
        malformed = [
            {"Alpha": {"x": 10}},                        # missing key
            {"Alpha": {"x": 10, "y": 20, "z": 30}},      # extra key
            {"Alpha": {"x": True, "y": 20}},             # bool
            {"Alpha": {"x": float("nan"), "y": 20}},     # NaN (via python object below)
            {"Alpha": {"x": "10", "y": 20}},             # string
            {"Alpha": [10, 20]},                         # not an object
            {"  Alpha": {"x": 10, "y": 20}},             # unnormalized name
            {"": {"x": 10, "y": 20}},                    # empty name
        ]
        for places in malformed:
            payload = {"schema_version": 1, "agent_id": self.ident.agent_id, "places": places}
            self.write_json(self.state / MEMORY_FILENAME, payload)
            with self.assertRaises(MemoryStoreError, msg=repr(places)):
                self.reload()

    def test_write_failure_preserves_old_file_and_state(self):
        """AT16: a failing write must not be reported as success and the
        previous valid file must remain readable."""
        self.store.remember_place("Alpha", 10, 20)
        before = (self.state / MEMORY_FILENAME).read_bytes()

        with unittest.mock.patch("os.replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.store.remember_place("Alpha", 99, 99)

        # failure not reported as success: in-memory state unchanged
        self.assertEqual(self.store.lookup_place("Alpha"), Position(x=10, y=20))
        # old file intact and reloadable
        self.assertEqual((self.state / MEMORY_FILENAME).read_bytes(), before)
        self.assertEqual(self.reload().lookup_place("Alpha"), Position(x=10, y=20))
        # temp file cleaned up
        leftovers = [p.name for p in self.state.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

        # store recovers after the failure
        self.store.remember_place("Alpha", 1, 2)
        self.assertEqual(self.reload().lookup_place("Alpha"), Position(x=1, y=2))


if __name__ == "__main__":
    unittest.main()
