"""Offline regression tests for immutable candidate and outcome history."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.translation_history import LocalJSONHistoryStore, write_json_atomic


class LocalHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = LocalJSONHistoryStore(self.root)

    def test_fresh_reader_preserves_unicode_and_exact_code_bytes(self):
        run = self.store.create_run({"model_name": "fake"})
        code = "# café\r\nx = 1\n"
        candidate = self.store.append(run, {"type": "candidate"}, code=code)
        records = LocalJSONHistoryStore(self.root).read_run(run)
        self.assertEqual(records[1]["record_id"], candidate)
        self.assertEqual(records[1]["code"], code)
        self.assertEqual(records[0]["metadata"], {"model_name": "fake"})

    def test_runs_and_identical_candidates_are_distinct(self):
        first = self.store.create_run({})
        second = self.store.create_run({})
        self.assertNotEqual(first, second)
        ids = [self.store.append(first, {"type": "candidate"}, code="pass") for _ in range(2)]
        self.assertNotEqual(*ids)
        self.assertEqual(len(self.store.read_run(first)), 3)
        self.assertEqual(len(self.store.read_run(second)), 1)

    def test_validation_values_use_strict_json_without_stringifying_numbers(self):
        run = self.store.create_run({})
        self.store.append(run, {"type": "outcome", "validation": {
            "passed": np.bool_(False), "count": np.int64(2), "difference": np.float64(0.5),
            "examples": [pd.NA, float("nan"), float("inf"), None, "001234"],
        }})
        validation = self.store.read_run(run)[1]["validation"]
        self.assertIs(validation["passed"], False)
        self.assertEqual(validation["count"], 2)
        self.assertEqual(validation["difference"], 0.5)
        self.assertEqual(validation["examples"], [
            {"__type__": "missing"}, {"__type__": "float", "value": "nan"},
            {"__type__": "float", "value": "inf"}, None, "001234",
        ])

    def test_failed_publish_is_not_visible_and_earlier_records_survive(self):
        run = self.store.create_run({})
        with patch.object(Path, "rename", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.store.append(run, {"type": "candidate"}, code="pass")
        self.assertEqual([r["type"] for r in self.store.read_run(run)], ["run"])
        self.store.append(run, {"type": "candidate"}, code="x = 1")
        self.assertEqual(len(self.store.read_run(run)), 2)

    def test_corrupt_code_is_reported(self):
        run = self.store.create_run({})
        candidate = self.store.append(run, {"type": "candidate"}, code="pass")
        (self.root / run / candidate / "code.py").write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.store.read_run(run)

    def test_rejects_path_ids_and_unknown_runs(self):
        for run in ("../outside", "C:/Windows", "a/b"):
            with self.subTest(run=run), self.assertRaises(ValueError):
                self.store.read_run(run)
        with self.assertRaises(FileNotFoundError):
            self.store.append("a" * 32, {"type": "candidate"}, code="pass")

    def test_atomic_pointer_failure_preserves_previous_pointer(self):
        pointer = self.root / "pointer.json"
        write_json_atomic(pointer, {"version": 1})
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_json_atomic(pointer, {"version": 2})
        self.assertEqual(json.loads(pointer.read_text()), {"version": 1})
        self.assertEqual(list(self.root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
