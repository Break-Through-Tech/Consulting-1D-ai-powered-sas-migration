"""Gemini's Program 0 code (tests/fixtures/program0_generated) runs and matches SAS (#82).

No Gemini call is made. The files were generated once, read, and saved. This checks
that the step runner, the macro and name handling and the validator still work
together on real code and the real input.
"""

import unittest
from pathlib import Path

from src import data_io, validation_report
from src.orchestrate import build_step_plan
from src.step_runner import run_saved_steps

ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "tests" / "fixtures" / "program0_generated"
PROGRAM0_OUTPUTS = ("LESS100_MEASURE", "MEASURE_AVERAGE_STDDEV_2025JUL", "STD_DATA_2025JUL_ANALYSIS")


class GeneratedProgram0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = build_step_plan(ROOT / "manifests" / "program0.json")
        # reviewed=True: every file in the fixture folder was read before it was saved.
        result = run_saved_steps(
            cls.plan, CODE_DIR, reviewed=True,
            initial_datasets={"HC.ALLDATA_2025JUL": data_io.load_input()},
        )
        cls.datasets = result["datasets"]
        tables = {name: table for name, table in cls.datasets.items() if not name.upper().startswith(("WORK.", "HC."))}
        cls.report = validation_report.build_report(tables, "Gemini code in tests/fixtures/program0_generated", programs=[0])

    def test_there_is_a_file_for_every_translated_step(self):
        expected = {step["id"] for step in self.plan if step["action"] == "translate"}

        self.assertEqual(expected, {path.stem for path in CODE_DIR.glob("*.py")})

    def test_the_three_program_0_outputs_are_produced(self):
        produced = {name.upper() for name in self.datasets}

        self.assertTrue(set(PROGRAM0_OUTPUTS) <= produced)

    def test_the_three_program_0_outputs_match_sas(self):
        for name in PROGRAM0_OUTPUTS:
            with self.subTest(output=name):
                entry = self.report["outputs"][name]
                self.assertEqual("PASS", entry["status"], validation_report.problem_lines(entry))

    def test_the_outputs_of_the_other_programs_are_not_claimed(self):
        others = [name for name, entry in self.report["outputs"].items() if entry["program"] != 0]

        self.assertTrue(others)
        self.assertEqual({"NOT_CHECKED"}, {self.report["outputs"][name]["status"] for name in others})


if __name__ == "__main__":
    unittest.main()
