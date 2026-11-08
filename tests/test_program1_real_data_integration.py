"""Verify the real-data pipeline through reviewed Program 1 and Program 2 against SAS."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src import data_io
from src.baseline import program0, program2
from src.program1_integration import run_reviewed_program1
from src.validate import compare_detailed


class RealSasPipelineTests(unittest.TestCase):
    def test_program0_to_reviewed_program1_to_program2(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            p0 = program0.run(data_io.load_input())
            input_csv = root / "program0_analysis.csv"
            p0.std_data_analysis.to_csv(input_csv, index=False)

            code_dir = root / "reviewed_code"
            code_dir.mkdir()

            (code_dir / "grp_score_macro.py").write_text(
                "from src.baseline.program1 import grp_score\n",
                encoding="utf-8",
            )

            for step in ("om", "os", "or", "ptexp", "process"):
                (code_dir / f"{step}.py").write_text(
                    "out_df = grp_score(input_df, measures)\n",
                    encoding="utf-8",
                )

            report = run_reviewed_program1(
                code_dir=code_dir,
                input_csv=input_csv,
                output_dir=root / "outputs",
                on_error="report",
            )

            self.assertEqual("PASS", report["status"], report)
            self.assertEqual(5, len(report["outputs"]))

            for table, output in report["outputs"].items():
                with self.subTest(table=table):
                    self.assertEqual("PASS", output["status"], output)

                    saved = pd.read_csv(
                        root / "outputs" / f"{table}.csv",
                        dtype={"PROVIDER_ID": str},
                    )
                    self.assertEqual(4566, len(saved))



            # Load the actual CSV files saved by reviewed Program 1.
            reviewed_outputs = {
                table: pd.read_csv(
                    root / "outputs" / f"{table}.csv",
                    dtype={"PROVIDER_ID": str},
                )
                for table in report["outputs"]
            }

            self.assertEqual(
                {
                    "OUTCOME_MORTALITY",
                    "OUTCOME_SAFETY",
                    "OUTCOME_READMISSION",
                    "PTEXP",
                    "PROCESS",
                },
                set(reviewed_outputs),
            )

            # Pass those saved outputs into Program 2.
            program2_outputs = program2.run(reviewed_outputs)

            self.assertEqual(
                {"star_2025jul", "national_average_2025jul"},
                set(program2_outputs),
            )

            # Verify both final outputs against the original SAS results.
            for name, actual in program2_outputs.items():
                with self.subTest(program2_output=name):
                    comparison = compare_detailed(
                        actual,
                        data_io.load_sas_output(name),
                        "PROVIDER_ID"
                        if name == "star_2025jul"
                        else None,
                    )
                    self.assertTrue(
                        comparison["passed"],
                        f"{name}: {comparison}",
                    )

    def test_group_execution_failure_reports_and_continues(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            p0 = program0.run(data_io.load_input())
            input_csv = root / "program0_analysis.csv"
            p0.std_data_analysis.to_csv(input_csv, index=False)

            code_dir = root / "reviewed_code"
            code_dir.mkdir()

            (code_dir / "grp_score_macro.py").write_text(
                "from src.baseline.program1 import grp_score\n",
                encoding="utf-8",
            )

            for step in ("om", "os", "or", "ptexp", "process"):
                code = (
                    'raise RuntimeError("intentional safety failure")\n'
                    if step == "os"
                    else "out_df = grp_score(input_df, measures)\n"
                )
                (code_dir / f"{step}.py").write_text(
                    code, encoding="utf-8"
                )

            output_dir = root / "outputs"
            report = run_reviewed_program1(
                code_dir=code_dir,
                input_csv=input_csv,
                output_dir=output_dir,
                on_error="report",
            )

            self.assertEqual("FAIL", report["status"])
            self.assertFalse(report["passed"])

            safety = report["outputs"]["OUTCOME_SAFETY"]
            self.assertEqual("ERROR", safety["status"], safety)
            self.assertEqual("RuntimeError", safety["error"]["type"])
            self.assertIn(
                "intentional safety failure",
                safety["error"]["message"],
            )

            safety_step = next(
                step for step in report["steps"]
                if step["id"] == "os"
            )
            self.assertEqual("execution", safety_step["stage"])

            self.assertFalse(
                (output_dir / "OUTCOME_SAFETY.csv").exists()
            )

            for table, result in report["outputs"].items():
                if table == "OUTCOME_SAFETY":
                    continue
                with self.subTest(table=table):
                    self.assertEqual("PASS", result["status"], result)
                    self.assertTrue(
                        (output_dir / f"{table}.csv").is_file()
                    )


if __name__ == "__main__":
    unittest.main()
