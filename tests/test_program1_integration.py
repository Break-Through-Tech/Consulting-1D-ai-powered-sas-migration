import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

from src.program1_integration import (
    generate_program1_code,
    run_reviewed_program1,
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
