"""run_steps_report keeps going past a failing step, and the run_program command (#75)."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src import data_io
from src.orchestrate import build_step_plan
from src.run_program import main
from src.step_runner import StepExecutionError, generate_step_code, run_steps_report
from tests.test_step_runner import FakeTranslator, data_step

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "program0_generated"


def plan_of_three():
    # a -> b -> c, and d on its own
    return [
        data_step("a", inputs=("WORK.IN",), outputs=("WORK.A",)),
        data_step("b", inputs=("WORK.A",), outputs=("WORK.B",), depends=["a"]),
        data_step("c", inputs=("WORK.B",), outputs=("WORK.C",), depends=["b"]),
        data_step("d", inputs=("WORK.IN",), outputs=("WORK.D",)),
    ]


CODE = {
    "a": "outputs = {'WORK.A': datasets['WORK.IN']}",
    "b": "outputs = {'WORK.B': datasets['WORK.A']}",
    "c": "outputs = {'WORK.C': datasets['WORK.B']}",
    "d": "outputs = {'WORK.D': datasets['WORK.IN']}",
}


class RunStepsReportTests(unittest.TestCase):
    def report(self, plan, code, **options):
        with tempfile.TemporaryDirectory() as directory:
            generate_step_code(FakeTranslator(code), plan, Path(directory) / "gen")
            return run_steps_report(
                plan, Path(directory) / "gen", reviewed=True,
                initial_datasets={"WORK.IN": pd.DataFrame({"x": [1]})}, **options,
            )

    def statuses(self, report):
        return {entry["id"]: entry["status"] for entry in report["steps"]}

    def test_all_steps_ok(self):
        report = self.report(plan_of_three(), CODE)

        self.assertEqual({"a": "ok", "b": "ok", "c": "ok", "d": "ok"}, self.statuses(report))
        self.assertEqual({"ok": 4, "skipped": 0, "failed": 0, "blocked": 0}, report["counts"])
        self.assertIn("WORK.C", report["datasets"])

    def test_a_failing_step_blocks_its_dependents_but_not_other_steps(self):
        report = self.report(plan_of_three(), {**CODE, "b": "1 / 0"})

        self.assertEqual({"a": "ok", "b": "failed", "c": "blocked", "d": "ok"}, self.statuses(report))
        by_id = {entry["id"]: entry for entry in report["steps"]}
        self.assertIn("ZeroDivisionError", by_id["b"]["error"])
        self.assertEqual(["b"], by_id["c"]["blocked_by"])
        self.assertIn("WORK.D", report["datasets"])
        self.assertNotIn("WORK.C", report["datasets"])

    def test_a_missing_declared_output_is_a_failure(self):
        report = self.report(plan_of_three(), {**CODE, "a": "outputs = {}"})

        self.assertEqual("failed", self.statuses(report)["a"])

    def test_a_runtime_macro_variable_that_is_not_set_is_a_failure(self):
        plan = [data_step("a", inputs=(), outputs=(), macro_vars={"n": "runtime (set in this step)"})]
        report = self.report(plan, {"a": "outputs = {}"})

        self.assertEqual("failed", self.statuses(report)["a"])
        self.assertIn("not set", report["steps"][0]["error"])

    def test_a_runtime_macro_variable_nobody_set_blocks_the_step(self):
        plan = [data_step("a", inputs=(), outputs=(), macro_vars={"n": "runtime (set by Program 1)"})]
        report = self.report(plan, {"a": "outputs = {}"})

        self.assertEqual("blocked", self.statuses(report)["a"])
        self.assertEqual(["macro variable n"], report["steps"][0]["blocked_by"])

    def test_a_skipped_context_step_does_not_block_anything(self):
        plan = [{**data_step("ctx", inputs=(), outputs=()), "action": "skip"},
                data_step("a", inputs=("WORK.IN",), outputs=("WORK.A",), depends=["ctx"])]
        with tempfile.TemporaryDirectory() as directory:
            generate_step_code(FakeTranslator(CODE), plan, Path(directory) / "gen")
            report = run_steps_report(plan, Path(directory) / "gen", reviewed=True,
                                      initial_datasets={"WORK.IN": pd.DataFrame({"x": [1]})})

        self.assertEqual({"ctx": "skipped", "a": "ok"}, self.statuses(report))

    def test_refuses_to_run_unless_reviewed_is_exactly_true(self):
        for flag in (False, None, "yes"):
            with self.subTest(flag=flag):
                with self.assertRaises(StepExecutionError):
                    run_steps_report(plan_of_three(), FIXTURE, reviewed=flag)

    def test_the_fixture_runs_every_program_0_step(self):
        plan = build_step_plan(ROOT / "manifests" / "program0.json")
        report = run_steps_report(plan, FIXTURE, reviewed=True,
                                  initial_datasets={"HC.ALLDATA_2025JUL": data_io.load_input()})

        self.assertEqual({"ok": 33, "skipped": 1, "failed": 0, "blocked": 0}, report["counts"])


class RunProgramCommandTests(unittest.TestCase):
    def run_command(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(args)
        return status, output.getvalue()

    def test_program_0_fixture_passes_and_exits_0(self):
        status, text = self.run_command(["0", "--code-dir", str(FIXTURE), "--reviewed"])

        self.assertEqual(0, status)
        self.assertIn("33 ok", text)
        for name in ("LESS100_MEASURE", "MEASURE_AVERAGE_STDDEV_2025JUL", "STD_DATA_2025JUL_ANALYSIS"):
            self.assertRegex(text, name + r"\s+PASS")

    def test_nothing_runs_without_reviewed(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(["0", "--code-dir", str(FIXTURE)])

        self.assertEqual(2, caught.exception.code)

    def test_program_2_with_another_programs_code_names_the_missing_steps(self):
        # Program 2 runs since #72, but never on a folder that lacks its steps.
        with self.assertRaises(FileNotFoundError) as caught:
            main(["2", "--code-dir", str(FIXTURE), "--reviewed"])

        self.assertIn("call_report", str(caught.exception))

    def test_a_missing_code_folder_is_reported(self):
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(SystemExit):
                main(["0", "--code-dir", "does-not-exist", "--reviewed"])

        self.assertIn("not found", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
