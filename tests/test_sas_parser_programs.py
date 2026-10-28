"""Checks on the generated Program 0 and Program 2 manifests (issue #36, Task #4).

These test the PARSER (are the steps, lines and datasets right?), not the
translation. Translation correctness is checked later by src/validate.py
against the SAS .sas7bdat answer keys (#38).

If a test fails after you edit the parser, regenerate the committed files:
    python -m src.sas_reader 0 --out manifests/program0.json
    python -m src.sas_reader 2 --out manifests/program2.json
"""

import json
import unittest
from pathlib import Path

from src.sas_reader.__main__ import PROGRAMS
from src.sas_reader.manifest import MACROS_FILE, parse_program
from src.sas_reader.parse import scan_statements

ROOT = Path(__file__).resolve().parent.parent

# Tables each program must produce: the SAS answer-key files in SAS Output/.
ANSWER_KEYS = {
    "0": {"LESS100_MEASURE", "MEASURE_AVERAGE_STDDEV_2025JUL", "STD_DATA_2025JUL_ANALYSIS"},
    "2": {"STAR_2025JUL", "NATIONAL_AVERAGE_2025JUL"},
}
# Tables a program may read without making them itself.
EXTERNAL_INPUTS = {
    "0": {"HC.ALLDATA_2025JUL"},                                  # the pipeline's raw input
    "2": {"STD_DATA_2025JUL_ANALYSIS", "OUTCOME_MORTALITY", "OUTCOME_SAFETY",
          "OUTCOME_READMISSION", "PTEXP", "PROCESS"},             # Program 0 and 1 outputs
}


def generate(p):
    return parse_program(PROGRAMS[p], MACROS_FILE, [PROGRAMS[n] for n in PROGRAMS if n < p])


class GeneratedManifests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = {p: generate(p) for p in ANSWER_KEYS}

    def test_no_unsupported_steps(self):
        for p, steps in self.steps.items():
            with self.subTest(program=p):
                self.assertEqual([s["id"] for s in steps if s["status"] != "supported"], [])

    def test_answer_key_tables_are_produced(self):
        for p, steps in self.steps.items():
            outputs = {o for s in steps for o in s["outputs"]}
            with self.subTest(program=p):
                self.assertLessEqual(ANSWER_KEYS[p], outputs)

    def test_every_input_comes_from_an_earlier_step(self):
        for p, steps in self.steps.items():
            made = set()
            for s in steps:
                if s["kind"] != "macro":
                    for x in s["inputs"]:
                        with self.subTest(program=p, step=s["id"], input=x):
                            self.assertTrue(x in made or x in EXTERNAL_INPUTS[p])
                made.update(s["outputs"])

    def test_depends_on_points_backwards(self):
        for p, steps in self.steps.items():
            seen = set()
            for s in steps:
                with self.subTest(program=p, step=s["id"]):
                    self.assertTrue(set(s["depends_on"]) <= seen)
                seen.add(s["id"])

    def test_every_statement_is_in_a_step(self):
        # No SAS code may be dropped between steps (comments and blank lines may).
        for p, steps in self.steps.items():
            text = PROGRAMS[p].read_text(encoding="utf-8", errors="replace")
            ranges = [(s["line_start"], s["line_end"]) for s in steps if s["kind"] != "macro"]
            for st in scan_statements(text)[0]:
                with self.subTest(program=p, line=st.first_line, stmt=st.text[:40]):
                    self.assertTrue(any(a <= st.first_line and st.last_line <= b for a, b in ranges))

    def test_sas_code_matches_file_lines(self):
        for p, steps in self.steps.items():
            for s in steps:
                lines = (ROOT / s["source_file"]).read_text(encoding="utf-8").splitlines()
                with self.subTest(program=p, step=s["id"]):
                    self.assertEqual(s["sas_code"], "\n".join(lines[s["line_start"] - 1:s["line_end"]]))

    def test_committed_manifest_is_up_to_date(self):
        for p, steps in self.steps.items():
            with self.subTest(program=p):
                committed = json.loads((ROOT / "manifests" / f"program{p}.json").read_text(encoding="utf-8"))
                self.assertEqual(committed, steps)


if __name__ == "__main__":
    unittest.main()
