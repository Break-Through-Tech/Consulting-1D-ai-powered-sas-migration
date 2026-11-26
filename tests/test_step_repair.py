"""Offline repair and history checks with reviewed fixtures and canned responses."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src import generate_steps, run_program
from src.step_repair import run_steps_with_repair
from src.step_runner import generate_step_code, run_saved_steps
from src.translation_history import CandidateHistory
from tests.test_step_codegen import SequenceTranslator
from tests.test_step_runner import FakeTranslator, data_step, macro_step


class RuntimeRepairTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.plan = [data_step("s", inputs=(), outputs=())]

    def records(self, folder):
        history = CandidateHistory(folder)
        return history.store.read_run(history.run_id)

    def run_loop(self, replies, review):
        translator = SequenceTranslator(replies)
        report = run_steps_with_repair(self.plan, self.folder, reviewed=True,
                                       translator_factory=lambda: translator, review_repair=review)
        return report, translator

    def test_repair_and_export_evidence_for_both_programs(self):
        for program, broken in [(0, "data_work_all_data_2025jul"), (2, "call_report")]:
            with self.subTest(program=program):
                folder = self.folder / str(program)
                fixture = run_program.ROOT / "tests" / "fixtures" / f"program{program}_generated"
                code = {p.stem: p.read_text(encoding="utf-8") for p in fixture.glob("*.py")}
                initial = FakeTranslator({**code, broken: "raise ValueError('injected runtime error')"})
                repair = FakeTranslator(code)
                args = [str(program), "--code-dir", str(folder)]
                with contextlib.redirect_stdout(io.StringIO()):
                    generate_steps.main(args, translator=initial)
                    with patch.object(run_program, "_repair_translator", return_value=repair), \
                         patch.object(run_program, "_review_repair", return_value=True) as review:
                        self.assertEqual(run_program.main([*args, "--reviewed", "--repair"]), 0)
                review.assert_called_once()
                self.assertIn("injected runtime error", repair.feedback)
                records = self.records(folder)
                self.assertIs(records[0]["metadata"]["repair_history"], True)
                self.assertEqual(records[0]["metadata"]["program"], program)
                request, = [r for r in records if r["type"] == "repair_request"]
                self.assertEqual(request["step_ids"], [broken])
                first, fixed = [r for r in records if r["type"] == "candidate" and r["step_id"] == broken]
                self.assertEqual(first["origin"], "generated")
                self.assertEqual(fixed["previous_candidate_id"], first["record_id"])
                self.assertEqual(fixed["repair_request_id"], request["record_id"])
                outcomes = [r for r in records if r["type"] == "outcome"]
                self.assertEqual([r["result"]["status"] for r in outcomes if r["candidate_id"] == first["record_id"]], ["failed"])
                self.assertEqual([r["result"]["status"] for r in outcomes if r["candidate_id"] == fixed["record_id"]], ["ok"])
                blocked = [r for r in outcomes if r["result"]["status"] == "blocked"]
                self.assertTrue(blocked)
                for r in blocked:
                    self.assertTrue(any(o["candidate_id"] == r["candidate_id"] and o["result"]["status"] == "ok"
                                        for o in outcomes))
                finished = records[-1]
                self.assertEqual((finished["type"], finished["status"]), ("run_finished", "PASS"))
                self.assertEqual(set(finished["outputs"]), set(run_program.PROGRAM_OUTPUTS[program]))
                self.assertTrue(all(o["status"] == "PASS" for o in finished["outputs"].values()))

    def test_syntax_and_runtime_requests_share_budget_across_replays(self):
        generate_step_code(SequenceTranslator(["bad syntax", "1 / 0"]), self.plan, self.folder)
        report, translator = self.run_loop(["1 / 0"], lambda step, code: True)
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertEqual(len(translator.feedbacks), 1)
        self.assertIn("ZeroDivisionError", translator.feedbacks[0])
        factory = Mock(side_effect=AssertionError("budget exhausted"))
        run_steps_with_repair(self.plan, self.folder, reviewed=True,
                              translator_factory=factory, review_repair=Mock())
        factory.assert_not_called()
        records = self.records(self.folder)
        self.assertEqual(sum(r["type"] == "repair_request" for r in records), 2)
        first, second, third = [r for r in records if r["type"] == "candidate"]
        self.assertEqual(second["previous_candidate_id"], first["record_id"])
        self.assertEqual(third["previous_candidate_id"], second["record_id"])
        self.assertEqual(next(r for r in records if r["type"] == "generation_error")["candidate_id"], first["record_id"])

    def test_decline_saves_candidate_without_executing_or_replacing_working_file(self):
        generate_step_code(SequenceTranslator(["1 / 0"]), self.plan, self.folder)
        report, _ = self.run_loop(["raise SystemExit('never execute')"], lambda step, code: 1)
        self.assertEqual(report["repair_status"], "REVIEW_REQUIRED")
        self.assertEqual((self.folder / "s.py").read_text(), "1 / 0")
        records = self.records(self.folder)
        rejected = next(r for r in records if r["type"] == "review")
        self.assertIs(rejected["approved"], False)
        pointer = CandidateHistory(self.folder).pointer
        first = next(r for r in records if r["type"] == "candidate")
        self.assertEqual(pointer["candidates"]["s"]["candidate_id"], first["record_id"])
        self.assertFalse(any(r.get("candidate_id") == rejected["candidate_id"] and r["type"] == "outcome"
                             for r in records))

    def test_fail_fast_saved_runner_records_only_reached_candidates(self):
        plan = [data_step(name, inputs=(), outputs=()) for name in ("a", "b", "c")]
        plan[1]["macro_vars"] = {"n": "runtime (set in this step)"}
        generate_step_code(FakeTranslator({s["id"]: "outputs = {}" for s in plan}), plan, self.folder)
        with self.assertRaisesRegex(ValueError, "were not returned"):
            run_saved_steps(plan, self.folder, reviewed=True)
        results = [r["result"] for r in self.records(self.folder) if r["type"] == "outcome"]
        self.assertEqual([(r["id"], r["status"]) for r in results], [("a", "ok"), ("b", "failed")])

    def test_macro_repair_unblocks_call_without_regenerating_it(self):
        plan = [macro_step(), data_step("call", inputs=(), outputs=(), depends=["double_macro"])]
        fixed = "def double(x): return x * 2"
        generate_step_code(FakeTranslator({"double_macro": fixed + "\n1 / 0",
                                           "call": "assert double(2) == 4\noutputs = {}"}), plan, self.folder)
        translator = Mock()
        translator.translate_macro_definition.return_value = fixed
        result = run_steps_with_repair(plan, self.folder, reviewed=True,
                                       translator_factory=lambda: translator, review_repair=lambda *_: True)
        self.assertEqual(result["counts"]["ok"], 2)
        step, name = translator.translate_macro_definition.call_args.args
        self.assertEqual((step["id"], name), ("double_macro", "double"))
        self.assertIn("ZeroDivisionError", translator.translate_macro_definition.call_args.kwargs["feedback"])
        translator.translate_step.assert_not_called()

    def test_interrupted_publication_reuses_approved_candidate_on_replay(self):
        generate_step_code(SequenceTranslator(["1 / 0"]), self.plan, self.folder)
        with patch.object(CandidateHistory, "select_candidate", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_loop(["outputs = {}"], lambda *_: True)
        self.assertEqual((self.folder / "s.py").read_text(), "outputs = {}")
        result, _ = self.run_loop([], lambda *_: self.fail("unexpected repair"))
        self.assertEqual(result["counts"]["ok"], 1)
        records = self.records(self.folder)
        approved, = [r["candidate_id"] for r in records if r["type"] == "review" and r["approved"]]
        self.assertEqual([r["candidate_id"] for r in records if r["type"] == "outcome"][-1], approved)
        self.assertEqual(sum(r["type"] == "candidate" for r in records), 2)

    def test_explicit_syntax_budget_must_be_between_one_and_three(self):
        translator = SequenceTranslator(["bad syntax"] * 3 + ["outputs = {}"])
        for limit in (4, 0, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                generate_step_code(translator, self.plan, self.folder / str(limit), max_syntax_attempts=limit)
        self.assertEqual(translator.feedbacks, [])

    def test_interrupted_publication_stops_when_working_bytes_do_not_match(self):
        generate_step_code(SequenceTranslator(["1 / 0"]), self.plan, self.folder)
        with patch("src.step_repair._publish_reviewed_code", side_effect=OSError("write interrupted")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.run_loop(["outputs = {}"], lambda *_: True)
        with self.assertRaisesRegex(ValueError, "Interrupted repair publication"):
            self.run_loop([], lambda *_: self.fail("inconsistent pointer reached review"))
        self.assertEqual((self.folder / "s.py").read_text(), "1 / 0")

    def test_missing_input_and_setup_errors_never_request_repairs(self):
        plan = [data_step("s", inputs=("MISSING",), outputs=())]
        generate_step_code(SequenceTranslator(["outputs = {}"]), plan, self.folder)
        factory = Mock(side_effect=AssertionError("a setup failure requested repair"))
        for datasets in ({}, {"MISSING": object()}):
            with self.subTest(datasets=datasets):
                report = run_steps_with_repair(plan, self.folder, reviewed=True, translator_factory=factory,
                                               review_repair=Mock(), initial_datasets=datasets)
                self.assertEqual(report["counts"]["blocked"], 1)
        factory.assert_not_called()

    def test_legacy_reviewed_files_are_repaired_in_a_copy_with_unknown_generation(self):
        (self.folder / "s.py").write_text("1 / 0")
        with patch("src.step_repair.IMPORT_DIR", self.folder / "imports"):
            result, _ = self.run_loop(["outputs = {}"], lambda *_: True)
        self.assertEqual(result["counts"]["ok"], 1)
        self.assertEqual((self.folder / "s.py").read_text(), "1 / 0")
        self.assertFalse((self.folder / "translation_history.json").exists())
        records = self.records(result["code_dir"])
        initial, fixed = [r for r in records if r["type"] == "candidate"]
        self.assertEqual(initial["origin"], "reviewed_file")
        self.assertEqual(fixed["previous_candidate_id"], initial["record_id"])

    def test_repair_transport_retry_records_one_fix_and_two_transport_attempts(self):
        generate_step_code(SequenceTranslator(["1 / 0"]), self.plan, self.folder)
        translator = Mock()
        translator.translate_step.side_effect = [TimeoutError("offline"), "outputs = {}"]
        with patch("src.pipeline_retry.time.sleep"):
            report = run_steps_with_repair(self.plan, self.folder, reviewed=True,
                                           translator_factory=lambda: translator, review_repair=lambda *_: True)
        self.assertEqual(report["counts"]["ok"], 1)
        records = self.records(self.folder)
        request, = [r for r in records if r["type"] == "repair_request"]
        error, = [r for r in records if r["type"] == "request_error"]
        self.assertEqual((error["repair_request_id"], error["request_number"]), (request["record_id"], 1))
        self.assertEqual(translator.translate_step.call_count, 2)
