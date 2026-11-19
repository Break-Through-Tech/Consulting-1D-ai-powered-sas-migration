"""Tests for retries and the generate, review, then run flow for step manifests (#73)."""

import tempfile
import unittest
import unittest.mock
from pathlib import Path

import pandas as pd

from src.step_runner import (
    SavedStepCode,
    StepExecutionError,
    StepRunner,
    generate_step_code,
    run_saved_steps,
)
from tests.test_step_runner import MACRO_CODE, FakeTranslator, approve, data_step, macro_step


class TemporaryError(Exception):
    """Looks like a temporary API failure to retry_call (HTTP 503)."""

    code = 503


class FlakyTranslator(FakeTranslator):
    """Fails with a temporary error a set number of times before answering."""

    def __init__(self, code, failures, error=TemporaryError):
        super().__init__(code)
        self.failures = failures
        self.error = error
        self.requests = 0

    def _maybe_fail(self):
        self.requests += 1
        if self.requests <= self.failures:
            raise self.error("try again")

    def translate_step(self, step, macro_vars, macros, feedback=None):
        self._maybe_fail()
        return super().translate_step(step, macro_vars, macros, feedback)


class SequenceTranslator(FakeTranslator):
    """Answers each request for a step with the next canned reply, and keeps the feedback."""

    def __init__(self, replies):
        super().__init__({})
        self.replies = list(replies)
        self.feedbacks = []

    def translate_step(self, step, macro_vars, macros, feedback=None):
        self.feedbacks.append(feedback)
        return self.replies.pop(0)


PLAN = [macro_step(), data_step(depends=["double_macro"])]
CODE = {
    "double_macro": MACRO_CODE,
    "call_double": "outputs = {'WORK.B': double(datasets['WORK.A'])}",
}


class RetryTests(unittest.TestCase):
    def test_temporary_error_is_retried(self):
        translator = FlakyTranslator({"s": "outputs = {}"}, failures=2)
        runner = StepRunner(translator, approve, retry_delay_seconds=0)
        result = runner.execute_step(data_step("s", inputs=(), outputs=()), {}, {})

        self.assertEqual(3, translator.requests)
        self.assertEqual({}, result["datasets"])

    def test_gives_up_after_max_attempts(self):
        translator = FlakyTranslator({"s": "outputs = {}"}, failures=5)
        runner = StepRunner(translator, approve, max_attempts=2, retry_delay_seconds=0)
        with self.assertRaises(Exception) as caught:
            runner.execute_step(data_step("s", inputs=(), outputs=()), {}, {})

        self.assertEqual(2, translator.requests)
        self.assertIn("2 attempt", str(caught.exception))

    def test_other_errors_are_not_retried(self):
        translator = FlakyTranslator({"s": "outputs = {}"}, failures=5, error=ValueError)
        runner = StepRunner(translator, approve, retry_delay_seconds=0)
        with self.assertRaises(ValueError):
            runner.execute_step(data_step("s", inputs=(), outputs=()), {}, {})

        self.assertEqual(1, translator.requests)


class GenerateTests(unittest.TestCase):
    def test_saves_one_file_per_translated_step_and_runs_nothing(self):
        plan = PLAN + [data_step("boom", inputs=(), outputs=())]
        code = {**CODE, "boom": "raise SystemExit('generated code must not run')\noutputs = {}"}
        with tempfile.TemporaryDirectory() as directory:
            files = generate_step_code(FakeTranslator(code), plan, Path(directory) / "gen")

            self.assertEqual({"double_macro", "call_double", "boom"}, set(files))
            self.assertEqual(MACRO_CODE, files["double_macro"].read_text(encoding="utf-8"))

    def test_call_steps_are_told_the_macro_signatures_without_running_the_macro(self):
        translator = FakeTranslator(CODE)
        with tempfile.TemporaryDirectory() as directory:
            generate_step_code(translator, PLAN, Path(directory) / "gen")

        self.assertEqual({"double": "double(df)"}, translator.asked[-1][3])

    def test_skipped_steps_get_no_file(self):
        plan = [{**data_step("ctx", inputs=(), outputs=()), "action": "skip"}]
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual({}, generate_step_code(FakeTranslator({}), plan, Path(directory) / "gen"))

    def test_refuses_a_directory_that_already_has_files(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "old.py").write_text("x = 1", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                generate_step_code(FakeTranslator(CODE), PLAN, directory)

    def test_rejects_code_that_is_not_valid_python(self):
        code = {**CODE, "call_double": "outputs = {"}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "not valid Python"):
                generate_step_code(FakeTranslator(code), PLAN, Path(directory) / "gen")

    def test_invalid_code_is_asked_again_with_the_error(self):
        bad = "x = 1; for m in [1]: x = m"
        translator = SequenceTranslator([bad, "outputs = {}"])
        with tempfile.TemporaryDirectory() as directory:
            files = generate_step_code(
                translator, [data_step("s", inputs=(), outputs=())], Path(directory) / "gen",
            )
            saved = files["s"].read_text(encoding="utf-8")

        self.assertEqual("outputs = {}", saved)
        self.assertIsNone(translator.feedbacks[0])
        self.assertIn("not valid Python", translator.feedbacks[1])

    def test_gives_up_when_the_code_stays_invalid(self):
        translator = SequenceTranslator(["x = (", "x = (", "x = ("])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "not valid Python"):
                generate_step_code(
                    translator, [data_step("s", inputs=(), outputs=())], Path(directory) / "gen",
                )

        self.assertEqual(3, len(translator.feedbacks))

    def test_rejects_macro_code_without_the_named_function(self):
        code = {**CODE, "double_macro": "def other(df):\n    return df\n"}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "'double'"):
                generate_step_code(FakeTranslator(code), PLAN, Path(directory) / "gen")

    def test_waits_between_requests_but_not_before_the_first(self):
        plan = [data_step(name, inputs=(), outputs=()) for name in ("a", "b", "c")]
        code = {name: "outputs = {}" for name in ("a", "b", "c")}
        with tempfile.TemporaryDirectory() as directory:
            with unittest.mock.patch("src.step_runner.time.sleep") as sleep:
                generate_step_code(FakeTranslator(code), plan, Path(directory) / "gen", min_interval_seconds=5)

        self.assertEqual([unittest.mock.call(5), unittest.mock.call(5)], sleep.call_args_list)

    def test_rejects_a_negative_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                generate_step_code(FakeTranslator({}), [], Path(directory) / "gen", min_interval_seconds=-1)

    def test_generation_retries_temporary_errors(self):
        translator = FlakyTranslator({"s": "outputs = {}"}, failures=1)
        with tempfile.TemporaryDirectory() as directory:
            generate_step_code(
                translator, [data_step("s", inputs=(), outputs=())], Path(directory) / "gen",
                retry_delay_seconds=0,
            )

        self.assertEqual(2, translator.requests)


class RunSavedTests(unittest.TestCase):
    def generate(self, directory):
        return generate_step_code(FakeTranslator(CODE), PLAN, Path(directory) / "gen")

    def test_runs_the_saved_files_and_makes_no_translator_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            self.generate(directory)
            result = run_saved_steps(
                PLAN, Path(directory) / "gen", reviewed=True,
                initial_datasets={"WORK.A": pd.DataFrame({"x": [1, 2]})},
            )

        self.assertEqual([2, 4], result["datasets"]["WORK.B"]["x"].tolist())

    def test_a_second_run_reuses_the_same_files(self):
        with tempfile.TemporaryDirectory() as directory:
            self.generate(directory)
            data = {"WORK.A": pd.DataFrame({"x": [3]})}
            first = run_saved_steps(PLAN, Path(directory) / "gen", reviewed=True, initial_datasets=data)
            second = run_saved_steps(PLAN, Path(directory) / "gen", reviewed=True, initial_datasets=data)

        self.assertEqual(first["datasets"]["WORK.B"]["x"].tolist(),
                         second["datasets"]["WORK.B"]["x"].tolist())

    def test_refuses_to_run_unless_reviewed_is_exactly_true(self):
        with tempfile.TemporaryDirectory() as directory:
            self.generate(directory)
            for flag in (False, None, "yes", 1):
                with self.subTest(flag=flag):
                    with self.assertRaisesRegex(StepExecutionError, "not been marked as reviewed"):
                        run_saved_steps(PLAN, Path(directory) / "gen", reviewed=flag)

    def test_missing_file_is_reported_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            files = self.generate(directory)
            files["call_double"].unlink()
            with self.assertRaisesRegex(FileNotFoundError, "call_double"):
                run_saved_steps(PLAN, Path(directory) / "gen", reviewed=True)

    def test_edits_made_during_review_are_what_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            files = self.generate(directory)
            files["call_double"].write_text(
                "outputs = {'WORK.B': datasets['WORK.A'].assign(x=99)}", encoding="utf-8",
            )
            result = run_saved_steps(
                PLAN, Path(directory) / "gen", reviewed=True,
                initial_datasets={"WORK.A": pd.DataFrame({"x": [1]})},
            )

        self.assertEqual([99], result["datasets"]["WORK.B"]["x"].tolist())

    def test_missing_directory_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            SavedStepCode("does-not-exist")


if __name__ == "__main__":
    unittest.main()
