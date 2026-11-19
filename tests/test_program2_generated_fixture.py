"""Gemini's Program 2 code (tests/fixtures/program2_generated) runs and matches SAS (#38).

No Gemini call is made. The files were generated, repaired with feedback, read, and
saved (see the folder's README). Program 2 starts from the SAS outputs of Programs 0
and 1, so this checks the handoff, the step runner, the SAS plug-ins and the
validator together on real code and real data.
"""

import unittest
from pathlib import Path

from src import validation_report
from src.orchestrate import build_step_plan
from src.run_program import program2_inputs
from src.step_runner import run_saved_steps

ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "tests" / "fixtures" / "program2_generated"
PROGRAM2_OUTPUTS = ("STAR_2025JUL", "NATIONAL_AVERAGE_2025JUL")


class GeneratedProgram2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = build_step_plan(ROOT / "manifests" / "program2.json")
        datasets, macro_vars = program2_inputs()
        # reviewed=True: every file in the fixture folder was read before it was saved.
        result = run_saved_steps(
            cls.plan, CODE_DIR, reviewed=True,
            initial_datasets=datasets, initial_macro_vars=macro_vars,
        )
        cls.datasets = result["datasets"]
        tables = {name: table for name, table in cls.datasets.items() if not name.upper().startswith(("WORK.", "HC."))}
        cls.report = validation_report.build_report(tables, "Gemini code in tests/fixtures/program2_generated", programs=[2])

    def test_there_is_a_file_for_every_translated_step(self):
        expected = {step["id"] for step in self.plan if step["action"] == "translate"}

        self.assertEqual(expected, {path.stem for path in CODE_DIR.glob("*.py")})

    def test_the_two_program_2_outputs_are_produced(self):
        produced = {name.upper() for name in self.datasets}

        self.assertTrue(set(PROGRAM2_OUTPUTS) <= produced)

    def test_the_two_program_2_outputs_match_sas(self):
        for name in PROGRAM2_OUTPUTS:
            with self.subTest(output=name):
                entry = self.report["outputs"][name]
                self.assertEqual("PASS", entry["status"], validation_report.problem_lines(entry))

    def test_the_outputs_of_the_other_programs_are_not_claimed(self):
        # Programs 0 and 1 tables are SAS inputs here, and a SAS table always matches itself.
        others = [name for name, entry in self.report["outputs"].items() if entry["program"] != 2]

        self.assertEqual(8, len(others))
        self.assertEqual({"NOT_CHECKED"}, {self.report["outputs"][name]["status"] for name in others})


if __name__ == "__main__":
    unittest.main()
