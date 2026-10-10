import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.validate import compare, compare_detailed


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_sas_csv(self, dataframe, name="sas.csv"):
        path = self.temp_path / name
        dataframe.to_csv(path, index=False)
        return path

    def test_exact_match_passes(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [1.5, 2.5],
        })

        sas_csv = self.write_sas_csv(sas)

        result = compare_detailed(
            sas.copy(),
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertTrue(result["passed"])

    def test_value_mismatch_fails(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [1.5, 2.5],
        })

        sas_csv = self.write_sas_csv(sas)

        py = sas.copy()
        py.loc[0, "SCORE"] = 99.0

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["mismatches"]["SCORE"]["count"],
            1,
        )

    def test_duplicate_python_ids_fail(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["101", "101"],
            "SCORE": [10.0, 20.0],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["duplicate_python_ids"],
            ["101"],
        )

    def test_duplicate_sas_ids_fail(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "101"],
            "SCORE": [10.0, 20.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = sas.copy()

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["duplicate_sas_ids"],
            ["101"],
        )

    def test_leading_zero_provider_ids_are_preserved(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["001", "002"],
            "SCORE": [10.0, 20.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["001", "002"],
            "SCORE": [10.0, 20.0],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertTrue(result["passed"])

    def test_missing_key_column_fails_without_crashing(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "SCORE": [10.0, 20.0],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "PROVIDER_ID",
            result["missing_columns"],
        )

    def test_blank_python_key_fails_without_crashing(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["101", ""],
            "SCORE": [10.0, 20.0],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["blank_python_keys"],
            1,
        )

    def test_blank_sas_key_fails_without_crashing(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", ""],
            "SCORE": [10.0, 20.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["blank_sas_keys"],
            1,
        )

    def test_missing_python_column_fails(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
            "COUNT": [2, 3],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["missing_columns"],
            ["COUNT"],
        )

    def test_extra_python_column_fails(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
            "EXTRA_COLUMN": ["x", "y"],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["extra_columns"],
            ["EXTRA_COLUMN"],
        )

    def test_column_order_mismatch_fails(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
            "COUNT": [2, 3],
        })

        sas_csv = self.write_sas_csv(sas)

        py = sas[
            [
                "PROVIDER_ID",
                "COUNT",
                "SCORE",
            ]
        ].copy()

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertFalse(
            result["column_order_match"]
        )

    def test_unmatched_provider_ids_fail(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "SCORE": [10.0, 20.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["101", "103"],
            "SCORE": [10.0, 20.0],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["missing_ids"],
            ["102"],
        )
        self.assertEqual(
            result["extra_ids"],
            ["103"],
        )

    def test_value_within_relative_tolerance_passes(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101"],
            "SCORE": [1_000_000.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["101"],
            "SCORE": [1_000_000.5],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertTrue(result["passed"])

    def test_value_outside_relative_tolerance_fails(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101"],
            "SCORE": [1_000_000.0],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["101"],
            "SCORE": [1_000_009.0],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])

    def test_blank_and_missing_text_are_equivalent(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "LABEL": ["A", None],
        })

        sas_csv = self.write_sas_csv(sas)

        py = pd.DataFrame({
            "PROVIDER_ID": ["101", "102"],
            "LABEL": ["A", ""],
        })

        result = compare_detailed(
            py,
            sas_csv,
            key="PROVIDER_ID",
        )

        self.assertTrue(result["passed"])

    def test_original_compare_interface_still_returns_bool(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101"],
            "SCORE": [10.0],
        })

        sas_csv = self.write_sas_csv(sas)

        result = compare(
            sas.copy(),
            sas_csv,
            key="PROVIDER_ID",
            tol=1e-6,
        )

        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
