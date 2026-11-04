"""The real pipeline, on the real CSVs, tells correct code from wrong code."""

import tempfile
import unittest
from pathlib import Path

from src.program1_integration import run_reviewed_program1

CORRECT_MACRO = "from src.baseline.program1 import grp_score\n"

# The same macro with one small bug: it standardizes with the population std.
WRONG_MACRO = """
from src.baseline.program1 import grp_score as correct_grp_score

def grp_score(df, varlist):
    out = correct_grp_score(df, varlist)
    population_std = out["score_before_std"].std(ddof=0)
    out["grp_score"] = (out["score_before_std"] - out["Mean"]) / population_std
    return out
"""

GROUP_STEP = "out_df = grp_score(input_df, measures)\n"
PROGRAM1_TABLES = ["OUTCOME_MORTALITY", "OUTCOME_SAFETY", "OUTCOME_READMISSION", "PTEXP", "PROCESS"]


def run_pipeline(root, macro):
    code_dir = root / "code"
    code_dir.mkdir()
    (code_dir / "grp_score_macro.py").write_text(macro)
    for step in ["om", "os", "or", "ptexp", "process"]:
        (code_dir / f"{step}.py").write_text(GROUP_STEP)

    return run_reviewed_program1(
        code_dir=code_dir,
        output_dir=root / "output",
        on_error="report",
    )


class RealPipelineTests(unittest.TestCase):
    def test_correct_code_passes_every_table(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_pipeline(Path(temp), CORRECT_MACRO)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(list(result["outputs"]), PROGRAM1_TABLES)

    def test_small_bug_fails_every_table(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_pipeline(Path(temp), WRONG_MACRO)

        self.assertEqual(result["status"], "FAIL")
        for table, output in result["outputs"].items():
            with self.subTest(table=table):
                self.assertEqual(output["status"], "FAIL")
                self.assertIn("GRP_SCORE", output["validation"]["mismatches"])


if __name__ == "__main__":
    unittest.main()
