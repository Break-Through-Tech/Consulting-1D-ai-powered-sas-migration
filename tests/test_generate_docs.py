"""The workflow documents are up to date and say what the SAS source says (#67)."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from src.generate_docs import PROGRAMS, ROOT, main, outside_inputs, permanent_outputs, render

DOCS = ROOT / "docs" / "workflow"
SAS_DIR = ROOT / "data" / "Project_1" / "SAS Programs"


def manifest(program):
    return json.loads((ROOT / "manifests" / f"program{program}.json").read_text(encoding="utf-8"))


def sas_line(step, offset=0):
    lines = (ROOT / step["source_file"]).read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[step["line_start"] - 1 + offset]


class WorkflowDocsTests(unittest.TestCase):
    def test_committed_documents_are_up_to_date(self):
        # If this fails: python -m src.generate_docs
        for program in PROGRAMS:
            committed = (DOCS / f"program{program}.md").read_text(encoding="utf-8")
            self.assertEqual(render(program), committed, f"program{program}.md is out of date")

    def test_check_flag_reports_stale_files(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(1, main(["--check", "--out-dir", directory]))
            self.assertEqual(0, main(["--out-dir", directory]))
            self.assertEqual(0, main(["--check", "--out-dir", directory]))
            (Path(directory) / "program0.md").write_text("old", encoding="utf-8")
            self.assertEqual(1, main(["--check", "--out-dir", directory]))

    def test_every_step_is_listed_with_its_source_lines(self):
        for program in PROGRAMS:
            text = render(program)
            for step in manifest(program):
                self.assertIn(f"| `{step['id']}` |", text)
                lines = (str(step["line_start"]) if step["line_start"] == step["line_end"]
                         else f"{step['line_start']}-{step['line_end']}")
                self.assertRegex(text, rf"\| `{re.escape(step['id'])}` \|.*{re.escape(Path(step['source_file']).name)} {lines} \|")

    def test_every_answer_key_table_is_named(self):
        expected = {
            0: {"LESS100_MEASURE", "MEASURE_AVERAGE_STDDEV_2025JUL", "STD_DATA_2025JUL_ANALYSIS"},
            1: {"OUTCOME_MORTALITY", "OUTCOME_READMISSION", "OUTCOME_SAFETY", "PROCESS", "PTEXP"},
            2: {"STAR_2025JUL", "NATIONAL_AVERAGE_2025JUL"},
        }
        for program, tables in expected.items():
            self.assertEqual(tables, set(permanent_outputs(manifest(program))))
            self.assertTrue(all(f"`{name}`" in render(program) for name in tables))

    def test_programs_are_connected_as_the_sas_connects_them(self):
        self.assertIn("**From Program 0:** `STD_DATA_2025JUL_ANALYSIS`", render(1))
        self.assertIn("**Used by Program 2:** `OUTCOME_MORTALITY`", render(1))
        self.assertIn("**From Program 1:** `OUTCOME_MORTALITY`", render(2))
        self.assertEqual(["HC.ALLDATA_2025JUL"], outside_inputs(manifest(0)))

    def test_macro_parameters_are_the_ones_in_the_sas_header(self):
        text = render(2)
        self.assertIn("| `%kmeans` | `in`, `out`, `grp` |", text)
        self.assertIn("| `%nation_avg_peer` | `indata`, `out`, `var`, `gr` |", text)
        self.assertIn("| `%grp_score` | `indsn`, `gp`, `varlist`, `nmeasure`, `Out_avg` |", render(1))

    def test_spot_check_rows_against_the_sas_source(self):
        # The step's first SAS line really is the statement the document says it is.
        by_id = {step["id"]: step for program in PROGRAMS for step in manifest(program)}
        expectations = {
            "call_kmeans": "%kmeans",
            "proc_sort_work_summary_score1": "proc sort",
            "proc_means_measure_average_stddev_2025jul": "proc means",
            "proc_standard_work_std_data_2025jul": "proc standard",
            "proc_transpose_work_measure_volume_t": "proc transpose",
        }
        for step_id, keyword in expectations.items():
            step = by_id[step_id]
            block = "\n".join(
                (ROOT / step["source_file"]).read_text(encoding="utf-8", errors="replace")
                .splitlines()[step["line_start"] - 1: step["line_end"]]
            ).lower()
            self.assertIn(keyword.lower(), block, step_id)


if __name__ == "__main__":
    unittest.main()
