import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

import pandas as pd

from src.translator import GeminiSASTranslator
from src.program1_integration import (
    generate_program1_code,
    run_reviewed_program1,
    run_program1_with_feedback,
)


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifests" / "program1.json"
CONFIG = ROOT / "config" / "measure_groups.json"


class FakeTranslator:
    """Return trusted test code without contacting Gemini."""

    MACRO_CODE = """
def grp_score(df, varlist):
    return pd.DataFrame({
        "PROVIDER_ID": df["PROVIDER_ID"],
        "score": df[varlist].sum(axis=1),
    })
"""

    def __init__(self):
        self.calls = []

    def translate_macro(self, step):
        self.calls.append(step["id"])
        return self.MACRO_CODE

    def translate_group_step(self, step, measures):
        self.calls.append(step["id"])
        return "out_df = grp_score(input_df, measures)"


class Program1IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)
        self.code_dir = self.root / "reviewed_code"
        self.output_dir = self.root / "python_outputs"
        self.sas_dir = self.root / "sas_references"

        self.sas_dir.mkdir()

        self.groups = json.loads(CONFIG.read_text(encoding="utf-8"))
        measures = sorted({
            measure
            for group in self.groups.values()
            for measure in group["measures"]
        })

        self.provider_ids = ["001234", "009876"]

        input_data = {
            "PROVIDER_ID": self.provider_ids,
        }

        for measure in measures:
            input_data[measure] = [1.0, 3.0]

        pd.DataFrame(input_data).to_csv(
            self.root / "input.csv",
            index=False,
        )

        # Synthetic references matching the trusted fake macro.
        for group in self.groups.values():
            count = len(group["measures"])
            expected = pd.DataFrame({
                "PROVIDER_ID": self.provider_ids,
                "score": [float(count), float(3 * count)],
            })
            expected.to_csv(
                self.sas_dir / f"{group['output_table']}.csv",
                index=False,
            )

        self.translator = FakeTranslator()

    def generate(self):
        return generate_program1_code(
            self.translator,
            code_dir=self.code_dir,
            manifest_path=MANIFEST,
            config_path=CONFIG,
        )

    def feedback_run(self, translator, **kwargs):
        return run_program1_with_feedback(
            translator, kwargs.pop("review_code", lambda files: True),
            run_dir=self.root / "feedback",
            input_csv=self.root / "input.csv",
            sas_output_dir=self.sas_dir, **kwargs,
        )

    def test_feedback_repairs_shared_macro_and_revalidates_all_groups(self):
        class RepairTranslator(FakeTranslator):
            def translate_macro(self, step):
                return self.MACRO_CODE.replace(".sum(axis=1)", ".sum(axis=1) + 1")

            def repair_program1(inner, steps, previous_code, feedback):
                self.assertEqual(len(feedback), 5)
                self.assertIn("mismatches", feedback["OUTCOME_SAFETY"])
                self.assertIn("+ 1", previous_code["grp_score_macro"])
                return {step["id"]: (inner.MACRO_CODE if step["kind"] == "macro"
                        else "out_df = grp_score(input_df, measures)")
                        for step in steps}

        reviews = []
        result = self.feedback_run(
            RepairTranslator(), review_code=lambda files: reviews.append(files) or True,
        )
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(len(result["outputs"]), 5)

    def test_feedback_stops_at_attempt_limit(self):
        class UnfixedTranslator(FakeTranslator):
            def translate_macro(self, step):
                return self.MACRO_CODE.replace(".sum(axis=1)", ".sum(axis=1) + 1")

            def repair_program1(self, steps, previous_code, feedback):
                return {step["id"]: previous_code[step["id"]] for step in steps}

        result = self.feedback_run(UnfixedTranslator(), max_attempts=2)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["attempts"]), 2)

    def test_feedback_does_not_repair_passing_run(self):
        result = self.feedback_run(FakeTranslator())
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["attempts"]), 1)

    def test_feedback_does_not_execute_without_review(self):
        result = self.feedback_run(FakeTranslator(), review_code=lambda files: False)
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["attempts"], [])
        self.assertFalse((self.root / "feedback" / "attempt_1" / "data").exists())

    def test_feedback_stops_on_execution_error(self):
        class BrokenTranslator(FakeTranslator):
            def translate_group_step(self, step, measures):
                return "raise ValueError('broken code')"

        result = self.feedback_run(BrokenTranslator())
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["attempts"]), 1)

    def test_feedback_rejects_invalid_limit(self):
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.feedback_run(FakeTranslator(), max_attempts=limit)

    def test_feedback_repairs_structural_validation_failure(self):
        class MissingColumnTranslator(FakeTranslator):
            def translate_group_step(inner, step, measures):
                if step["id"] == "os":
                    return "out_df = input_df[['PROVIDER_ID']].copy()"
                return super().translate_group_step(step, measures)

            def repair_program1(inner, steps, previous_code, feedback):
                self.assertEqual(set(feedback), {"OUTCOME_SAFETY"})
                self.assertEqual(feedback["OUTCOME_SAFETY"]["missing_columns"], ["SCORE"])
                self.assertEqual({step["id"] for step in steps}, {"grp_score_macro", "os"})
                return {"grp_score_macro": inner.MACRO_CODE,
                        "os": "out_df = grp_score(input_df, measures)"}

        result = self.feedback_run(MissingColumnTranslator())
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["attempts"]), 2)

    def test_feedback_detects_regressions_in_previously_passing_groups(self):
        class RegressionTranslator(FakeTranslator):
            def translate_group_step(inner, step, measures):
                code = super().translate_group_step(step, measures)
                return code + ("\nout_df['score'] += 1" if step["id"] == "os" else "")

            def repair_program1(inner, steps, previous_code, feedback):
                self.assertEqual(set(feedback), {"OUTCOME_SAFETY"})
                return {
                    "grp_score_macro": inner.MACRO_CODE.replace(".sum(axis=1)", ".sum(axis=1) + 1"),
                    "os": "out_df = grp_score(input_df, measures)\nout_df['score'] -= 1",
                }

        result = self.feedback_run(RegressionTranslator(), max_attempts=2)
        self.assertFalse(result["passed"])
        self.assertEqual(result["outputs"]["OUTCOME_SAFETY"]["status"], "PASS")
        self.assertEqual(result["outputs"]["OUTCOME_MORTALITY"]["status"], "FAIL")

    def test_feedback_retries_transient_repair_api_failure(self):
        class FlakyRepairTranslator(FakeTranslator):
            repair_calls = 0

            def translate_macro(inner, step):
                return inner.MACRO_CODE.replace(".sum(axis=1)", ".sum(axis=1) + 1")

            def repair_program1(inner, steps, previous_code, feedback):
                inner.repair_calls += 1
                if inner.repair_calls == 1:
                    raise TimeoutError("temporary repair API timeout")
                return {step["id"]: (inner.MACRO_CODE if step["kind"] == "macro"
                        else previous_code[step["id"]]) for step in steps}

        service = FlakyRepairTranslator()
        with patch("src.pipeline_retry.time.sleep"):
            result = self.feedback_run(service)
        self.assertTrue(result["passed"])
        self.assertEqual(service.repair_calls, 2)
        self.assertEqual(len(result["attempts"]), 2)

    def test_feedback_single_attempt_does_not_call_repair(self):
        class NoRepairTranslator(FakeTranslator):
            def translate_macro(inner, step):
                return inner.MACRO_CODE.replace(".sum(axis=1)", ".sum(axis=1) + 1")

            def repair_program1(inner, *args):
                self.fail("No repair allowed with max_attempts=1")

        result = self.feedback_run(NoRepairTranslator(), max_attempts=1)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["attempts"]), 1)

    def test_feedback_rejects_incomplete_repair_before_execution(self):
        class InvalidRepair(FakeTranslator):
            def translate_macro(self, step):
                return self.MACRO_CODE.replace(".sum(axis=1)", ".sum(axis=1) + 1")

            def repair_program1(self, steps, previous_code, feedback):
                return {}

        result = self.feedback_run(InvalidRepair())
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "ValueError")
        self.assertIn("each requested step", result["error"]["message"])
        self.assertEqual(len(result["attempts"]), 1)
        self.assertFalse((self.root / "feedback" / "attempt_2" / "data").exists())

    def test_feedback_can_decline_corrected_code(self):
        class RepairTranslator(FakeTranslator):
            def translate_macro(self, step):
                return self.MACRO_CODE.replace(".sum(axis=1)", ".sum(axis=1) + 1")

            def repair_program1(self, steps, previous_code, feedback):
                return {step["id"]: previous_code[step["id"]] for step in steps}

        reviews = []
        def review(files):
            reviews.append(files)
            return len(reviews) == 1

        result = self.feedback_run(RepairTranslator(), review_code=review)
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(len(result["attempts"]), 1)
        self.assertFalse((self.root / "feedback" / "attempt_2" / "data").exists())

    def repair_service(self):
        """Use the real repair adapter with a fake SDK and faulty initial code."""
        service = GeminiSASTranslator.__new__(GeminiSASTranslator)
        service.client = MagicMock()
        service.model_name = "fake-model"
        service.translate_macro = MagicMock(return_value=FakeTranslator.MACRO_CODE.replace(
            ".sum(axis=1)", ".sum(axis=1) + 1",
        ))
        service.translate_group_step = MagicMock(
            return_value="out_df = grp_score(input_df, measures)",
        )
        return service

    def repair_response(self, macro=None):
        code = {"grp_score_macro": macro or FakeTranslator.MACRO_CODE}
        code.update({step: "out_df = grp_score(input_df, measures)"
                     for step in ("om", "os", "or", "ptexp", "process")})
        return MagicMock(text=json.dumps(code))

    def assert_repair_error_history(self, result, completed=1):
        self.assertEqual(result["status"], "ERROR")
        self.assertFalse(result["passed"])
        self.assertEqual(result["error"]["stage"], "repair")
        self.assertEqual(result["error"]["attempt"], completed + 1)
        self.assertEqual(result["attempt"], completed + 1)
        self.assertEqual(len(result["attempts"]), completed)
        self.assertEqual(result["outputs"], result["attempts"][-1]["outputs"])
        self.assertEqual(result["steps"], result["attempts"][-1]["steps"])
        for index, report in enumerate(result["attempts"], 1):
            self.assertEqual(report["attempt"], index)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(len(report["outputs"]), 5)
            self.assertTrue(report["outputs"]["OUTCOME_SAFETY"]["validation"]["mismatches"])
            self.assertTrue(Path(report["code_dir"]).is_dir())
        self.assertFalse((Path(result["code_dir"]).parent / "data").exists())
        json.dumps(result)  # No exception objects or circular references in reports.

    def test_feedback_preserves_history_on_malformed_json(self):
        service = self.repair_service()
        service.client.models.generate_content.return_value.text = "not JSON"
        result = self.feedback_run(service)
        self.assert_repair_error_history(result)
        self.assertEqual(result["error"]["type"], "JSONDecodeError")
        self.assertEqual(result["error"]["attempts"], 1)
        service.client.models.generate_content.assert_called_once()

    def test_feedback_counts_timeout_then_malformed_json(self):
        service = self.repair_service()
        service.client.models.generate_content.side_effect = [
            TimeoutError("temporary"), MagicMock(text="not JSON"),
        ]
        with patch("src.pipeline_retry.time.sleep"):
            result = self.feedback_run(service)
        self.assert_repair_error_history(result)
        self.assertEqual(result["error"]["type"], "JSONDecodeError")
        self.assertEqual(result["error"]["attempts"], 2)
        self.assertEqual(service.client.models.generate_content.call_count, 2)

    def test_feedback_counts_timeout_then_permanent_api_error(self):
        from google.genai import errors

        service = self.repair_service()
        service.client.models.generate_content.side_effect = [
            TimeoutError("temporary"),
            errors.ClientError(403, {"error": {"message": "permission denied"}}),
        ]
        with patch("src.pipeline_retry.time.sleep"):
            result = self.feedback_run(service)
        self.assert_repair_error_history(result)
        self.assertEqual(result["error"]["type"], "ClientError")
        self.assertEqual(result["error"]["cause_type"], "ClientError")
        self.assertEqual(result["error"]["attempts"], 2)
        self.assertEqual(service.client.models.generate_content.call_count, 2)

    def check_invalid_reference(self, blank, mixed=False):
        path = self.sas_dir / "OUTCOME_SAFETY.csv"
        reference = pd.read_csv(path, dtype={"PROVIDER_ID": "string"})
        reference.loc[1, "PROVIDER_ID"] = None if blank else reference.loc[0, "PROVIDER_ID"]
        reference.to_csv(path, index=False)
        service = self.repair_service() if mixed else FakeTranslator()
        if mixed:
            # All groups initially have a score defect; Safety also has bad reference keys.
            response = {"grp_score_macro": FakeTranslator.MACRO_CODE}
            response.update({step: "out_df = grp_score(input_df, measures)"
                             for step in ("om", "or", "ptexp", "process")})
            service.client.models.generate_content.return_value.text = json.dumps(response)
        else:
            service.repair_program1 = MagicMock(side_effect=AssertionError("Must not repair"))
        result = self.feedback_run(service)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["non_repairable_outputs"], ["OUTCOME_SAFETY"])
        self.assertEqual(len(result["attempts"]), 2 if mixed else 1)
        field = "blank_sas_keys" if blank else "duplicate_sas_ids"
        for attempt in result["attempts"]:
            self.assertTrue(attempt["outputs"]["OUTCOME_SAFETY"]["validation"][field])
        if mixed:
            service.client.models.generate_content.assert_called_once()
            request = json.loads(service.client.models.generate_content.call_args.kwargs["contents"])
            self.assertNotIn("OUTCOME_SAFETY", request["validation_feedback"])
            self.assertNotIn("os", {step["id"] for step in request["steps"]})
            self.assertEqual(len(request["validation_feedback"]), 4)
            self.assertTrue(all(output["status"] == "PASS" for table, output in result["outputs"].items()
                                if table != "OUTCOME_SAFETY"))
        else:
            service.repair_program1.assert_not_called()

    def test_feedback_does_not_repair_duplicate_reference_ids(self):
        self.check_invalid_reference(blank=False)

    def test_feedback_does_not_repair_blank_reference_keys(self):
        self.check_invalid_reference(blank=True)

    def test_feedback_repairs_other_groups_with_duplicate_reference_ids(self):
        self.check_invalid_reference(blank=False, mixed=True)

    def test_feedback_repairs_other_groups_with_blank_reference_keys(self):
        self.check_invalid_reference(blank=True, mixed=True)

    def test_feedback_preserves_history_on_invalid_response_shape(self):
        for index, text in enumerate(("{}", "[]", "null", '{"grp_score_macro": 42}', "", None)):
            with self.subTest(response=text):
                service = self.repair_service()
                service.client.models.generate_content.return_value.text = text
                result = run_program1_with_feedback(
                    service, lambda files: True,
                    run_dir=self.root / f"invalid_{index}",
                    input_csv=self.root / "input.csv", sas_output_dir=self.sas_dir,
                )
                self.assert_repair_error_history(result)
                self.assertIn(result["error"]["type"], {"ValueError", "JSONDecodeError", "TypeError"})
                service.client.models.generate_content.assert_called_once()

    def test_feedback_preserves_history_on_permanent_api_error(self):
        from google.genai import errors

        service = self.repair_service()
        service.client.models.generate_content.side_effect = errors.ClientError(
            403, {"error": {"message": "permission denied"}},
        )
        result = self.feedback_run(service)
        self.assert_repair_error_history(result)
        self.assertEqual(result["error"]["type"], "ClientError")
        self.assertEqual(result["error"]["cause_type"], "ClientError")
        self.assertIn("permission denied", result["error"]["message"])
        service.client.models.generate_content.assert_called_once()

    def test_feedback_preserves_history_on_exhausted_api_retries(self):
        service = self.repair_service()
        service.client.models.generate_content.side_effect = TimeoutError("repair offline")
        with patch("src.pipeline_retry.time.sleep") as sleep:
            result = self.feedback_run(service)
        self.assert_repair_error_history(result)
        self.assertEqual(result["error"]["type"], "RetryFailure")
        self.assertEqual(result["error"]["cause_type"], "TimeoutError")
        self.assertEqual(result["error"]["attempts"], 3)
        self.assertEqual(service.client.models.generate_content.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.0, 4.0])

    def test_feedback_preserves_multiple_completed_attempts_on_api_failure(self):
        service = self.repair_service()
        wrong_macro = service.translate_macro.return_value
        service.client.models.generate_content.side_effect = [
            self.repair_response(wrong_macro), TimeoutError("offline"),
            TimeoutError("offline"), TimeoutError("offline"),
        ]
        with patch("src.pipeline_retry.time.sleep"):
            result = self.feedback_run(service)
        self.assert_repair_error_history(result, completed=2)
        self.assertEqual(result["error"]["attempts"], 3)
        self.assertEqual(service.client.models.generate_content.call_count, 4)

    def test_feedback_preserves_history_when_review_callback_raises(self):
        service = self.repair_service()
        service.client.models.generate_content.return_value = self.repair_response()
        review = MagicMock(side_effect=[True, RuntimeError("review unavailable")])
        result = self.feedback_run(service, review_code=review)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["stage"], "review")
        self.assertEqual(result["error"]["type"], "RuntimeError")
        self.assertEqual(len(result["attempts"]), 1)
        self.assertFalse((Path(result["code_dir"]).parent / "data").exists())

    def test_feedback_initial_generation_failure_returns_empty_history(self):
        service = self.repair_service()
        service.translate_macro.side_effect = ValueError("invalid translation")
        result = self.feedback_run(service)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["stage"], "generation")
        self.assertEqual(result["attempts"], [])
        self.assertEqual(result["outputs"], {})
        self.assertEqual(result["steps"], [])
        service.client.models.generate_content.assert_not_called()

    def test_feedback_reports_preexecution_file_error(self):
        (self.root / "input.csv").unlink()
        result = self.feedback_run(FakeTranslator())
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["stage"], "execution")
        self.assertEqual(result["error"]["type"], "FileNotFoundError")
        self.assertEqual(result["attempts"], [])

    def test_feedback_preserves_history_on_repair_file_write_error(self):
        service = self.repair_service()
        service.client.models.generate_content.return_value = self.repair_response()
        original_write = Path.write_text

        def write_candidate(path, *args, **kwargs):
            if "attempt_2" in path.parts:
                raise PermissionError("cannot save repair")
            return original_write(path, *args, **kwargs)

        with patch.object(Path, "write_text", autospec=True, side_effect=write_candidate):
            result = self.feedback_run(service)
        self.assert_repair_error_history(result)
        self.assertEqual(result["error"]["type"], "PermissionError")

    def test_feedback_invalid_configuration_fails_before_creating_run(self):
        with self.assertRaises(FileNotFoundError):
            self.feedback_run(FakeTranslator(), manifest_path=self.root / "missing.json")
        self.assertFalse((self.root / "feedback").exists())

    def test_feedback_syntax_error_in_repair_stops_after_validation_history(self):
        service = self.repair_service()
        service.client.models.generate_content.return_value = self.repair_response(
            "def grp_score(: invalid syntax",
        )
        result = self.feedback_run(service)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(result["attempts"][0]["status"], "FAIL")
        macro = next(step for step in result["steps"] if step["id"] == "grp_score_macro")
        self.assertEqual(macro["error"]["type"], "SyntaxError")
        self.assertTrue(all(item["status"] == "BLOCKED" for item in result["outputs"].values()))
        service.client.models.generate_content.assert_called_once()

    def test_feedback_real_adapter_with_mocked_sdk_repairs_and_validates(self):
        service = GeminiSASTranslator.__new__(GeminiSASTranslator)
        service.client = MagicMock()
        service.model_name = "fake-model"
        wrong_macro = FakeTranslator.MACRO_CODE.replace(".sum(axis=1)", ".sum(axis=1) + 1")
        responses = [MagicMock(text=json.dumps({"python_code": wrong_macro}))]
        responses += [MagicMock(text=json.dumps({
            "python_code": "out_df = grp_score(input_df, measures)",
        })) for _ in range(5)]
        service.client.models.generate_content.side_effect = responses + [self.repair_response()]
        result = self.feedback_run(service)
        self.assertTrue(result["passed"])
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(service.client.models.generate_content.call_count, 7)

    def test_feedback_three_attempt_success_uses_latest_code(self):
        service = self.repair_service()
        still_wrong = FakeTranslator.MACRO_CODE.replace(".sum(axis=1)", ".sum(axis=1) + 2")
        service.client.models.generate_content.side_effect = [
            self.repair_response(still_wrong), self.repair_response(),
        ]
        result = self.feedback_run(service)
        self.assertTrue(result["passed"])
        self.assertEqual([item["status"] for item in result["attempts"]], ["FAIL", "FAIL", "PASS"])
        last_request = json.loads(service.client.models.generate_content.call_args.kwargs["contents"])
        self.assertEqual(last_request["previous_code"]["grp_score_macro"], still_wrong)
        self.assertEqual(last_request["validation_feedback"], {
            table: item["validation"] for table, item in result["attempts"][1]["outputs"].items()
        })

    def test_feedback_existing_run_directory_preserves_artifacts(self):
        directory = self.root / "feedback"
        directory.mkdir()
        sentinel = directory / "existing.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.feedback_run(FakeTranslator())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_feedback_truthy_review_value_does_not_execute(self):
        result = self.feedback_run(FakeTranslator(), review_code=lambda files: 1)
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertIsNone(result["error"])
        self.assertFalse((Path(result["code_dir"]).parent / "data").exists())

    def test_feedback_declined_review_cannot_resume_in_wrapper(self):
        result = self.feedback_run(FakeTranslator(), review_code=lambda files: False)
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        with self.assertRaises(FileExistsError):
            self.feedback_run(FakeTranslator())

    def execute(self):
        return run_reviewed_program1(
            code_dir=self.code_dir,
            input_csv=self.root / "input.csv",
            output_dir=self.output_dir,
            manifest_path=MANIFEST,
            config_path=CONFIG,
            sas_output_dir=self.sas_dir,
        )

    def test_generation_creates_all_six_files_without_execution(self):
        generated = self.generate()

        self.assertEqual(
            set(generated),
            {"grp_score_macro", "om", "os", "or", "ptexp", "process"},
        )
        self.assertTrue(all(path.is_file() for path in generated.values()))
        self.assertFalse(self.output_dir.exists())

        self.assertEqual(
            self.translator.calls,
            ["grp_score_macro", "om", "os", "or", "ptexp", "process"],
        )

    def test_all_five_groups_pass_validation(self):
        self.generate()
        result = self.execute()

        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["outputs"]), 5)

        for group in self.groups.values():
            table = group["output_table"]
            self.assertEqual(result["outputs"][table]["status"], "PASS")
            self.assertTrue((self.output_dir / f"{table}.csv").exists())

        self.assertEqual(result["steps"][0]["status"], "SKIPPED")

    def test_sas_mismatch_is_reported(self):
        self.generate()

        table = "OUTCOME_SAFETY"
        path = self.sas_dir / f"{table}.csv"
        reference = pd.read_csv(path, dtype={"PROVIDER_ID": "string"})
        reference.loc[0, "score"] += 100
        reference.to_csv(path, index=False)

        result = self.execute()

        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["outputs"][table]["status"], "FAIL")

    def test_missing_reviewed_code_stops_before_execution(self):
        self.generate()
        (self.code_dir / "os.py").unlink()

        with self.assertRaises(FileNotFoundError):
            self.execute()

        self.assertFalse(self.output_dir.exists())

    def test_reviewed_run_reports_execution_failure(self):
        self.generate()

        (self.code_dir / "os.py").write_text(
            "raise RuntimeError('intentional execution failure')",
            encoding="utf-8",
        )

        result = run_reviewed_program1(
            code_dir=self.code_dir,
            input_csv=self.root / "input.csv",
            output_dir=self.output_dir,
            manifest_path=MANIFEST,
            config_path=CONFIG,
            sas_output_dir=self.sas_dir,
            on_error="report",
        )

        self.assertEqual("FAIL", result["status"])
        self.assertFalse(result["passed"])

        safety = result["outputs"]["OUTCOME_SAFETY"]
        self.assertEqual("ERROR", safety["status"])
        self.assertEqual("RuntimeError", safety["error"]["type"])

        safety_step = next(
            step for step in result["steps"]
            if step["id"] == "os"
        )
        self.assertEqual("execution", safety_step["stage"])

        for table in (
            "OUTCOME_MORTALITY",
            "OUTCOME_READMISSION",
            "PTEXP",
            "PROCESS",
        ):
            self.assertEqual(
                "PASS",
                result["outputs"][table]["status"],
            )

    def test_non_dataframe_output_is_rejected(self):
        self.generate()

        (self.code_dir / "os.py").write_text(
            "out_df = 42",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(TypeError, "DataFrame"):
            self.execute()

    def test_transient_translation_failure_is_retried(self):
        class FlakyTranslator(FakeTranslator):
            def translate_macro(self, step):
                self.calls.append(step["id"])
                if self.calls.count(step["id"]) == 1:
                    raise TimeoutError("temporary translation timeout")
                return self.MACRO_CODE

        self.translator = FlakyTranslator()

        with patch("src.pipeline_retry.time.sleep"):
            generated = self.generate()

        self.assertEqual(len(generated), 6)
        self.assertEqual(
            self.translator.calls.count("grp_score_macro"),
            2,
        )

    def test_permanent_translation_error_is_not_retried(self):
        class InvalidTranslator(FakeTranslator):
            def translate_macro(self, step):
                self.calls.append(step["id"])
                raise ValueError("invalid translation input")

        self.translator = InvalidTranslator()

        with self.assertRaisesRegex(
            ValueError,
            "invalid translation input",
        ):
            self.generate()

        self.assertEqual(
            ["grp_score_macro"],
            self.translator.calls,
        )

    def test_generation_does_not_overwrite_existing_code(self):
        self.generate()

        with self.assertRaises(FileExistsError):
            self.generate()


    def test_each_group_receives_independent_input_copy(self):
        self.generate()

        # Mortality changes a Safety measure that it does not use.
        # Safety must still receive the original input data.
        safety_measure = self.groups["OS"]["measures"][0]
        self.assertNotIn(
            safety_measure,
            self.groups["OM"]["measures"],
        )

        (self.code_dir / "om.py").write_text(
            f"input_df[{safety_measure!r}] = -999.0\n"
            "out_df = grp_score(input_df, measures)\n",
            encoding="utf-8",
        )

        result = self.execute()

        self.assertTrue(result["passed"])
        self.assertEqual(
            result["outputs"]["OUTCOME_SAFETY"]["status"],
            "PASS",
        )

    def test_stale_outputs_cleared_before_execution_failure(self):
        self.generate()
        self.output_dir.mkdir()

        # Simulate CSVs remaining from an earlier successful run.
        for group in self.groups.values():
            table = group["output_table"]
            (self.output_dir / f"{table}.csv").write_text(
                "stale output\n",
                encoding="utf-8",
            )

        # Mortality runs first; Safety then fails deliberately.
        (self.code_dir / "os.py").write_text(
            "raise RuntimeError('intentional test failure')\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "intentional test failure",
        ):
            self.execute()

        # Mortality produced a fresh CSV before the failure.
        mortality = self.output_dir / "OUTCOME_MORTALITY.csv"
        self.assertTrue(mortality.exists())
        self.assertNotEqual(
            mortality.read_text(encoding="utf-8"),
            "stale output\n",
        )

        # The remaining groups must not retain stale CSVs.
        for group in self.groups.values():
            table = group["output_table"]
            if table != "OUTCOME_MORTALITY":
                self.assertFalse(
                    (self.output_dir / f"{table}.csv").exists()
                )


if __name__ == "__main__":
    unittest.main()
