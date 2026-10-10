"""Checks that the hand-written Reader files match the real SAS and CSV files.

Run from the repo root:  python -m unittest discover tests -v
"""
import csv
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = ROOT / "data" / "Project_1" / "SAS Output CSV"

REQUIRED_FIELDS = {
    "id", "title", "sas_code", "source_file", "line_start", "line_end",
    "inputs", "outputs", "macros_called", "macro_vars", "notes",
}
EXPECTED_COUNTS = {"OM": 7, "OS": 8, "OR": 11, "PtExp": 8, "Process": 12}
# Columns that follow C1..Cn in every group output file.
TAIL_COLUMNS = ["total_cnt", "measure_wt", "score_before_std", "Mean", "StdDev", "grp_score"]


def load_json(relative_path):
    with open(ROOT / relative_path, encoding="utf-8") as f:
        return json.load(f)


def csv_header(table_name):
    with open(CSV_DIR / f"{table_name}.csv", encoding="utf-8", newline="") as f:
        return next(csv.reader(f))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.steps = load_json("manifests/program1.json")

    def test_every_step_has_all_fields(self):
        for step in self.steps:
            missing = REQUIRED_FIELDS - set(step)
            self.assertFalse(
                missing,
                f"{step['id']}: missing required fields {sorted(missing)}",
            )

    def test_step_ids_are_unique(self):
        ids = [s["id"] for s in self.steps]
        self.assertEqual(len(ids), len(set(ids)))

    def test_sas_code_matches_real_file_lines(self):
        # Guards against copy/paste typos and wrong line numbers.
        for step in self.steps:
            lines = (ROOT / step["source_file"]).read_text(encoding="utf-8").splitlines()
            real = "\n".join(lines[step["line_start"] - 1:step["line_end"]])
            self.assertEqual(real, step["sas_code"], step["id"])

    def test_group_steps_call_the_macro(self):
        for step in self.steps:
            if step["id"] in {s.lower() for s in EXPECTED_COUNTS}:
                self.assertIn("grp_score", step["macros_called"])
                self.assertIn("%grp_score(", step["sas_code"])


class MeasureGroupTests(unittest.TestCase):
    def setUp(self):
        self.groups = load_json("config/measure_groups.json")

    def test_measure_counts(self):
        counts = {key: len(g["measures"]) for key, g in self.groups.items()}
        self.assertEqual(EXPECTED_COUNTS, counts)
        self.assertEqual(46, sum(counts.values()))

    def test_measures_exist_in_input_csv(self):
        input_header = set(csv_header("STD_DATA_2025JUL_ANALYSIS"))
        for key, group in self.groups.items():
            for measure in group["measures"]:
                self.assertIn(measure, input_header, f"{key}: {measure}")

    def test_no_measure_is_in_two_groups(self):
        all_measures = [m for g in self.groups.values() for m in g["measures"]]
        self.assertEqual(len(all_measures), len(set(all_measures)))

    def test_output_columns_match_sas_output_csv(self):
        # Measure order in the config must equal the order SAS wrote.
        for key, group in self.groups.items():
            n = len(group["measures"])
            expected = (["PROVIDER_ID"] + group["measures"]
                        + [f"C{i}" for i in range(1, n + 1)] + TAIL_COLUMNS)
            self.assertEqual(expected, csv_header(group["output_table"]), key)


if __name__ == "__main__":
    unittest.main()
