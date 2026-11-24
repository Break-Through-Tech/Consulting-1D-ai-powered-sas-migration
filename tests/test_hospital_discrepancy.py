"""Tests for hospital-level SAS discrepancy diagnostics."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.data_io import SAS_OUTPUT_DIR
from src.hospital_discrepancy import (
    CHECKPOINTS,
    compare_hospital,
    values_match,
)


ROOT = Path(__file__).resolve().parent.parent


class HospitalDiscrepancyTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name)
        (self.run / "data").mkdir()

        self.sas_tables = {}

        for program, name in CHECKPOINTS:
            if program == 0:
                column = "std_measure"
                sas_column = "STD_MEASURE"
                value = 0.25
            elif program == 1:
                column = sas_column = "grp_score"
                value = 0.30
            else:
                column = sas_column = "summary_score"
                value = 0.17

            python_table = pd.DataFrame({
                "PROVIDER_ID": ["010001", "010002"],
                column: [value, value + 0.1],
            })

            sas_table = pd.DataFrame({
                "PROVIDER_ID": ["010001", "010002"],
                sas_column: [value, value + 0.1],
            })

            python_table.to_csv(
                self.run / "data" / f"{name}.csv",
                index=False,
            )

            self.sas_tables[name] = sas_table

    def tearDown(self):
        self.temp.cleanup()

    def compare(self, provider_id="010001"):
        with patch(
            "src.hospital_discrepancy.load_sas_output",
            side_effect=lambda name: self.sas_tables[name].copy(),
        ):
            return compare_hospital(self.run, provider_id)

    def change_python_value(self, name, column, new_value):
        path = self.run / "data" / f"{name}.csv"

        table = pd.read_csv(
            path, dtype={"PROVIDER_ID": str}
        )

        table.loc[
            table["PROVIDER_ID"] == "010001",
            column,
        ] = new_value

        table.to_csv(path, index=False)

    def test_matching_hospital_passes_all_checkpoints(self):
        result = self.compare()

        self.assertEqual(len(result["checks"]), 7)
        self.assertTrue(
            all(check["status"] == "PASS"
                for check in result["checks"])
        )
        self.assertIsNone(
            result["diagnostics"]["earliest_failing_stage"]
        )

    def test_mortality_mismatch_points_to_program1(self):
        self.change_python_value(
            "OUTCOME_MORTALITY", "grp_score", 0.55
        )

        result = self.compare()
        diagnostics = result["diagnostics"]

        self.assertEqual(
            1, diagnostics["earliest_failing_stage"]
        )
        self.assertEqual(
            ["OUTCOME_MORTALITY"],
            diagnostics["earliest_failing_outputs"],
        )

        trace = diagnostics["failed_outputs"][
            "OUTCOME_MORTALITY"
        ]

        self.assertIn("om", trace["investigate_first"])
        self.assertIn(
            "grp_score_macro",
            trace["investigate_first"],
        )

    def test_program0_mismatch_takes_priority(self):
        self.change_python_value(
            "STD_DATA_2025JUL_ANALYSIS",
            "std_measure",
            0.9,
        )
        self.change_python_value(
            "OUTCOME_MORTALITY", "grp_score", 0.55
        )
        self.change_python_value(
            "STAR_2025JUL", "summary_score", 0.8
        )

        result = self.compare()
        diagnostics = result["diagnostics"]

        self.assertEqual(
            0, diagnostics["earliest_failing_stage"]
        )
        self.assertEqual(
            ["STD_DATA_2025JUL_ANALYSIS"],
            diagnostics["earliest_failing_outputs"],
        )
        self.assertEqual(
            {1, 2},
            {
                item["program"]
                for item in diagnostics["later_stage_failures"]
            },
        )

    def test_program2_only_mismatch(self):
        self.change_python_value(
            "STAR_2025JUL", "summary_score", 0.8
        )

        result = self.compare()

        self.assertEqual(
            2,
            result["diagnostics"]["earliest_failing_stage"],
        )
        self.assertEqual(
            ["STAR_2025JUL"],
            result["diagnostics"]["earliest_failing_outputs"],
        )

    def test_case_insensitive_column_names(self):
        result = self.compare()

        program0 = result["checks"][0]

        self.assertEqual(
            "STD_DATA_2025JUL_ANALYSIS",
            program0["output"],
        )
        self.assertEqual("PASS", program0["status"])

    def test_validation_tolerance(self):
        self.assertTrue(
            values_match(0.25, 0.25 + 1e-11)
        )
        self.assertFalse(
            values_match(0.25, 0.26)
        )

    def test_missing_values(self):
        self.assertTrue(
            values_match(None, float("nan"))
        )
        self.assertTrue(values_match("", None))
        self.assertFalse(values_match(None, 0.0))

    def test_missing_column_is_reported(self):
        path = self.run / "data/OUTCOME_MORTALITY.csv"
        table = pd.read_csv(
            path, dtype={"PROVIDER_ID": str}
        )
        table.drop(columns=["grp_score"]).to_csv(
            path, index=False
        )

        result = self.compare()

        mortality = next(
            check for check in result["checks"]
            if check["output"] == "OUTCOME_MORTALITY"
        )

        self.assertEqual("FAIL", mortality["status"])
        self.assertTrue(
            any(
                difference["reason"]
                == "Column missing from one output"
                for difference in mortality["differences"]
            )
        )

    def test_unknown_hospital_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError, "not found"
        ):
            self.compare("999999")


class RealSavedRunTests(unittest.TestCase):

    def test_real_hospital_matches_sas(self):
        run = (
            ROOT
            / "output/chain_runs/20261120T224507Z_6ed62743"
        )

        if not (run / "data/STAR_2025JUL.csv").exists():
            self.skipTest("Saved pipeline run unavailable")

        if not all(
            (
                SAS_OUTPUT_DIR
                / f"{name.lower()}.sas7bdat"
            ).exists()
            for _, name in CHECKPOINTS
        ):
            self.skipTest("SAS reference files unavailable")

        result = compare_hospital(run, "010001")

        self.assertEqual(len(result["checks"]), 7)
        self.assertTrue(
            all(check["status"] == "PASS"
                for check in result["checks"])
        )


if __name__ == "__main__":
    unittest.main()
