import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.migration_results import build_results, history_result, main, repair_attempt_counts
from src.program1_integration import generate_program1_code, run_reviewed_program1
from src.translation_history import LocalJSONHistoryStore


class FixtureTranslator:
    """Provide the saved reviewed Python through the normal history writer."""

    def translate_macro(self, step):
        fixture = Path(__file__).resolve().parent / "fixtures" / "program1_generated"
        return (fixture / f"{step['id']}.py").read_text(encoding="utf-8")

    def translate_group_step(self, step, measures):
        return self.translate_macro(step)


class MigrationResultsTests(unittest.TestCase):
    def test_legacy_cap_rejection_only_removes_a_repair_that_never_dispatched(self):
        legacy_cap_error = {"type": "ValueError", "message": "Measurement stopped at the 50-request limit."}
        records = [
            {"type": "repair_request", "record_id": "before", "step_ids": ["never_dispatched"]},
            {"type": "request_error", "repair_request_id": "before", "request_number": 1, "error": legacy_cap_error},
            {"type": "repair_request", "record_id": "after", "step_ids": ["retried"]},
            {"type": "request_error", "repair_request_id": "after", "request_number": 1,
             "error": {"type": "RuntimeError", "message": "Gemini returned HTTP status 503"}},
            {"type": "request_error", "repair_request_id": "after", "request_number": 2, "error": legacy_cap_error},
            {"type": "repair_request", "step_ids": ["old_history_without_dispatch_details"]},
        ]
        self.assertEqual(repair_attempt_counts(records), {"retried": 1, "old_history_without_dispatch_details": 1})

    def test_interrupted_replay_does_not_inherit_a_finished_pass(self):
        records = [
            {"type": "run", "run_id": "r", "metadata": {"program": 0, "repair_history": True}},
            {"type": "candidate", "record_id": "c", "step_id": "s", "origin": "generated"},
            {"type": "execution_started", "execution_id": "one", "candidates": {"s": "c"}},
            {"type": "outcome", "execution_id": "one", "candidate_id": "c", "result": {"id": "s", "status": "ok"}},
            {"type": "run_finished", "execution_id": "one", "status": "PASS", "step_statuses": {"s": "ok"}},
            {"type": "execution_started", "execution_id": "two", "candidates": {"s": "c"}},
            {"type": "outcome", "execution_id": "two", "candidate_id": "c", "result": {"id": "s", "status": "failed"}},
            {"type": "repair_request", "step_ids": ["s"]},
        ]
        for i, record in enumerate(records):
            record.update(sequence=i, recorded_at="same")
        run = history_result(0, records)
        self.assertEqual(run["status"], "INCOMPLETE")
        self.assertEqual(run["steps"], [{"id": "s", "first_try_success": True, "status": "failed", "fix_attempts": 1}])
        self.assertEqual(run["outputs"], {})
        self.assertEqual(history_result(0, list(reversed(records))), run)
        for record in records:
            del record["sequence"]
        legacy = history_result(0, records)
        self.assertIsNone(legacy["steps"][0]["first_try_success"])
        self.assertNotEqual(legacy["status"], "PASS")

    def test_program1_linked_attempt_directories_export_without_measurement(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous_id = previous_candidate = None
            for n, status in enumerate(("FAIL", "PASS"), 1):
                code_dir = root / f"attempt_{n}" / "code"
                store = LocalJSONHistoryStore(code_dir / ".translation_history")
                metadata = {"program": 1, "attempt": n, "repair_history": True}
                if n == 2:
                    metadata.update(loop_id=previous_id, previous_run_id=previous_id)
                run_id = store.create_run(metadata)
                candidate = {"type": "candidate", "step_id": "s", "origin": "generated"}
                if n == 2:
                    store.append(run_id, {"type": "repair_request", "step_ids": ["s"]})
                    candidate.update(origin="repair", previous_candidate_id=previous_candidate)
                candidate_id = store.append(run_id, candidate, code="pass")
                step = {"id": "s", "kind": "group", "group_config": {"output_table": "OUTCOME_SAFETY"}}
                store.append(run_id, {"type": "execution_started", "execution_id": str(n),
                                      "execution_plan": [step], "candidates": {"s": candidate_id}})
                store.append(run_id, {"type": "outcome", "execution_id": str(n), "candidate_id": candidate_id,
                                      "result": {"id": "s", "kind": "group", "status": status}})
                (code_dir / "translation_history.json").write_text(json.dumps({"run_id": run_id}))
                previous_id, previous_candidate = run_id, candidate_id
            store.append(run_id, {"type": "feedback_status", "attempt": 2, "status": "PASS"})
            output = root / "result.json"
            main(["--program1-run", str(root), "--output", str(output)])
            result, = json.loads(output.read_text())["runs"]
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["steps"], [{"id": "s", "first_try_success": False, "status": "pass", "fix_attempts": 1}])
            self.assertEqual(result["outputs"]["OUTCOME_SAFETY"]["status"], "PASS")
            self.assertIsNone(result["elapsed_seconds"])
            main(["--history", "1", str(code_dir / ".translation_history"), run_id, "--output", str(output)])
            self.assertEqual(json.loads(output.read_text())["runs"], [result])
            # An interrupted next attempt cannot reuse the previous terminal status.
            (root / "attempt_3").mkdir()
            with self.assertRaises(ValueError):
                main(["--program1-run", str(root), "--output", str(output)])

    def test_history_preserves_unknown_first_tries_and_counts_repairs(self):
        records = [
            {"type": "run", "run_id": "run", "metadata": {"program": 0, "repair_history": True}},
            {"type": "candidate", "record_id": "a", "step_id": "step_a", "origin": "generated"},
            {"type": "candidate", "record_id": "b", "step_id": "step_b", "origin": "generated"},
            {"type": "candidate", "record_id": "c", "step_id": "step_c", "origin": "reviewed_file"},
            {"type": "outcome", "candidate_id": "a", "result": {"status": "failed"}},
            {"type": "outcome", "candidate_id": "b", "result": {"status": "blocked"}},
            {"type": "repair_request", "step_ids": ["step_a"]},
            {"type": "candidate", "record_id": "d", "step_id": "step_a", "origin": "repaired",
             "previous_candidate_id": "a"},
            {"type": "outcome", "candidate_id": "d", "result": {"status": "ok"}},
            {"type": "outcome", "candidate_id": "b", "result": {"status": "ok"}},
            {"type": "outcome", "candidate_id": "c", "result": {"status": "ok"}},
        ]
        result = history_result(0, records)

        self.assertEqual(0.5, result["first_try_success_rate"])
        self.assertEqual(2, result["first_try_measured"])
        self.assertEqual(
            [("step_a", False, 1), ("step_b", True, 0), ("step_c", None, 0)],
            [(row["id"], row["first_try_success"], row["fix_attempts"]) for row in result["steps"]],
        )

    def test_old_history_does_not_assume_zero_repairs(self):
        result = history_result(1, [
            {"type": "run", "run_id": "run", "metadata": {}},
            {"type": "candidate", "record_id": "a", "step_id": "om", "origin": "generated"},
            {"type": "outcome", "candidate_id": "a", "result": {"status": "PASS"}},
        ])

        self.assertTrue(result["steps"][0]["first_try_success"])
        self.assertIsNone(result["steps"][0]["fix_attempts"])
        self.assertIsNone(result["cost_usd"])



    def test_legacy_completion_counts_only_the_macro(self):
        records = [{"type": "run", "run_id": "legacy", "metadata": {}}]
        for step_id, kind in (("grp_score_macro", "macro"), ("om", "group")):
            records.extend([
                {"type": "candidate", "record_id": step_id, "step_id": step_id,
                 "origin": "generated", "step": {"id": step_id, "kind": kind}},
                {"type": "execution_started", "candidate_id": step_id,
                 "entry_point": "run_translator", "review_status": "not_recorded"},
                {"type": "outcome", "candidate_id": step_id, "status": "COMPLETE",
                 "validation_status": "not_run"},
            ])

        result = history_result(1, records)
        steps = {step["id"]: step for step in result["steps"]}

        self.assertTrue(steps["grp_score_macro"]["first_try_success"])
        self.assertIsNone(steps["om"]["first_try_success"])
        self.assertIsNone(result["status"])
        self.assertEqual({}, result["outputs"])


class Program1HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            code_dir = root / "code"
            output_dir = root / "outputs"
            generate_program1_code(FixtureTranslator(), code_dir=code_dir)
            run_id = json.loads((code_dir / "translation_history.json").read_text())["run_id"]
            store = LocalJSONHistoryStore(code_dir / ".translation_history")

            cls.reviewed = run_reviewed_program1(code_dir=code_dir, output_dir=output_dir)
            cls.complete_records = store.read_run(run_id)

            # The real runner records the early failure and raises before the other groups.
            (code_dir / "om.py").write_text('raise RuntimeError("stopped at om")\n', encoding="utf-8")
            try:
                run_reviewed_program1(code_dir=code_dir, output_dir=output_dir)
            except RuntimeError as error:
                if str(error) != "stopped at om":
                    raise
            else:
                raise AssertionError("Expected the edited group script to fail")
            cls.partial_records = store.read_run(run_id)

    def test_export_recovers_reviewed_macro_and_all_output_results(self):
        result = history_result(1, self.complete_records)

        self.assertEqual("PASS", result["status"])
        self.assertEqual(6, result["first_try_measured"])
        self.assertEqual(6, result["first_try_passed"])
        self.assertEqual(1.0, result["first_try_success_rate"])
        steps = {step["id"]: step for step in result["steps"]}
        self.assertEqual("complete", steps["grp_score_macro"]["status"])
        self.assertTrue(steps["grp_score_macro"]["first_try_success"])
        self.assertTrue(all(step["fix_attempts"] is None for step in steps.values()))
        self.assertEqual(self.reviewed["outputs"].keys(), result["outputs"].keys())
        for table, output in result["outputs"].items():
            with self.subTest(table=table):
                expected = self.reviewed["outputs"][table]
                gaps = [
                    stats["max_abs_diff"]
                    for stats in expected["validation"]["column_stats"].values()
                    if stats["max_abs_diff"] is not None
                ]
                self.assertEqual(
                    {"program": 1, "status": expected["status"],
                     "largest_difference": max(gaps, default=None)},
                    output,
                )

    def test_latest_incomplete_execution_does_not_borrow_earlier_outcomes(self):
        result = history_result(1, self.partial_records)

        self.assertEqual("INCOMPLETE", result["status"])
        self.assertEqual("ERROR", result["outputs"]["OUTCOME_MORTALITY"]["status"])
        self.assertEqual("NOT_CHECKED", result["outputs"]["OUTCOME_SAFETY"]["status"])
        self.assertIsNone(result["outputs"]["OUTCOME_SAFETY"]["largest_difference"])
        steps = {step["id"]: step for step in result["steps"]}
        self.assertEqual("error", steps["om"]["status"])
        self.assertIsNone(steps["os"]["status"])

    def test_candidate_link_identifies_original_even_with_reversed_record_order(self):
        records = self.partial_records.copy()
        original, edited = [
            index for index, record in enumerate(records)
            if record["type"] == "candidate" and record["step_id"] == "om"
        ]
        records[original], records[edited] = records[edited], records[original]

        result = history_result(1, records)
        step = next(step for step in result["steps"] if step["id"] == "om")

        self.assertTrue(step["first_try_success"])
        self.assertIsNone(step["fix_attempts"])


class HistoricalObservationsTests(unittest.TestCase):
    def test_program1_reports_passing_runs_without_assuming_total(self):
        observations = build_results([])["historical_observations"]
        program1 = next(entry for entry in observations if entry["program"] == 1)

        self.assertEqual(5, program1["runs_passing_all_outputs"])
        self.assertIsNone(program1["gemini_runs"])
