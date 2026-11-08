import contextlib
import io
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
        
    def test_dataframe_reference_is_accepted(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["010001", "010005"],
            "SCORE": [1.5, 2.5],
        })

        result = compare_detailed(
            sas.copy(),
            sas,
            key="PROVIDER_ID",
        )

        self.assertTrue(result["passed"])

        changed = sas.copy()
        changed.loc[1, "SCORE"] = 9.9

        result = compare_detailed(
            changed,
            sas,
            key="PROVIDER_ID",
        )

        self.assertFalse(result["passed"])
        self.assertIn("SCORE", result["mismatches"])

    def test_csv_and_dataframe_references_use_the_same_tolerance(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101"],
            "SCORE": [1.0],
        })

        py = pd.DataFrame({
            "PROVIDER_ID": ["101"],
            "SCORE": [1.0000001],
        })

        # A 1e-7 relative difference is too large for either reference.
        result = compare_detailed(py, sas, key="PROVIDER_ID")
        self.assertFalse(result["passed"])

        sas_csv = self.write_sas_csv(sas)
        result = compare_detailed(py, sas_csv, key="PROVIDER_ID")
        self.assertFalse(result["passed"])

        
    def test_default_tolerances_are_not_relaxed(self):
        # Our coach's rule (#40): look at failing rows instead of raising these.
        for sas_value, py_value in [(1_000_000.0, 1_000_000.002), (0.0, 1e-11)]:
            with self.subTest(sas_value=sas_value):
                sas = pd.DataFrame({"PROVIDER_ID": ["101"], "SCORE": [sas_value]})
                py = pd.DataFrame({"PROVIDER_ID": ["101"], "SCORE": [py_value]})

                self.assertFalse(compare_detailed(py, sas, key="PROVIDER_ID")["passed"])

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
            "SCORE": [1_000_000.0005],
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
            "SCORE": [1_000_000.5],
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

    def test_no_key_compares_single_row_table(self):
        sas = pd.DataFrame({
            "SUMMARY_SCORE_NAT": [-0.034354],
            "PEER3": [-0.168412],
        })

        result = compare_detailed(sas.copy(), sas, key=None)

        self.assertTrue(result["passed"])
        self.assertTrue(result["key_found"])
        self.assertEqual(
            result["python_columns"],
            ["SUMMARY_SCORE_NAT", "PEER3"],
        )

    def test_no_key_reports_changed_value_by_column(self):
        sas = pd.DataFrame({
            "SUMMARY_SCORE_NAT": [-0.034354],
            "PEER3": [-0.168412],
        })

        py = pd.DataFrame({
            "SUMMARY_SCORE_NAT": [-0.034354],
            "PEER3": [0.5],
        })

        result = compare_detailed(py, sas, key=None)

        self.assertFalse(result["passed"])
        self.assertEqual(list(result["mismatches"]), ["PEER3"])
        self.assertEqual(result["mismatches"]["PEER3"]["count"], 1)

    def test_no_key_compares_rows_by_position(self):
        sas = pd.DataFrame({"SCORE": [1.0, 2.0]})
        py = pd.DataFrame({"SCORE": [2.0, 1.0]})

        result = compare_detailed(py, sas, key=None)

        self.assertFalse(result["passed"])
        self.assertEqual(result["mismatches"]["SCORE"]["count"], 2)

    def test_no_key_extra_row_fails(self):
        sas = pd.DataFrame({"SCORE": [1.0]})
        py = pd.DataFrame({"SCORE": [1.0, 2.0]})

        result = compare_detailed(py, sas, key=None)

        self.assertFalse(result["passed"])
        self.assertFalse(result["row_count_match"])
        self.assertEqual(result["extra_ids"], ["1"])

    def test_no_key_accepts_csv_reference(self):
        sas = pd.DataFrame({"SCORE": [1.5]})

        sas_csv = self.write_sas_csv(sas)

        result = compare_detailed(sas.copy(), sas_csv, key=None)

        self.assertTrue(result["passed"])

    def test_compare_interface_accepts_no_key(self):
        sas = pd.DataFrame({"SCORE": [1.5]})

        with contextlib.redirect_stdout(io.StringIO()):
            result = compare(sas.copy(), sas, key=None)

        self.assertTrue(result)

    def test_named_key_aligns_stat_rows_in_any_order(self):
        sas = pd.DataFrame({
            "_STAT_": ["N", "MEAN"],
            "MORT_30_AMI": [2002.0, 0.125946],
        })

        py = pd.DataFrame({
            "_STAT_": ["MEAN", "N"],
            "MORT_30_AMI": [0.125946, 2002.0],
        })

        result = compare_detailed(py, sas, key="_STAT_")

        self.assertTrue(result["passed"])

    def test_empty_tables_with_same_columns_pass(self):
        sas = pd.DataFrame({
            "freq": pd.Series(dtype="float64"),
            "measure_name": pd.Series(dtype="object"),
        })

        py = pd.DataFrame(columns=["freq", "measure_name"])

        result = compare_detailed(py, sas, key="measure_name")

        self.assertTrue(result["passed"])

    def test_empty_reference_with_rows_in_output_names_the_row(self):
        sas = pd.DataFrame({
            "freq": pd.Series(dtype="float64"),
            "measure_name": pd.Series(dtype="object"),
        })

        py = pd.DataFrame({
            "freq": [50.0],
            "measure_name": ["OP_22"],
        })

        result = compare_detailed(py, sas, key="measure_name")

        self.assertFalse(result["passed"])
        self.assertEqual(result["extra_ids"], ["OP_22"])

    def test_key_missing_from_both_tables_is_reported(self):
        sas = pd.DataFrame({"SCORE": [1.5]})

        result = compare_detailed(sas.copy(), sas, key="PROVIDER_ID")

        self.assertFalse(result["passed"])
        self.assertFalse(result["key_found"])
        self.assertEqual(result["missing_columns"], [])

    def test_key_missing_from_one_table_is_reported(self):
        sas = pd.DataFrame({
            "PROVIDER_ID": ["101"],
            "SCORE": [1.5],
        })

        py = pd.DataFrame({"SCORE": [1.5]})

        result = compare_detailed(py, sas, key="PROVIDER_ID")

        self.assertFalse(result["key_found"])

    def test_key_missing_only_from_sas_table_is_reported(self):
        sas = pd.DataFrame({"SCORE": [1.5]})

        py = pd.DataFrame({
            "PROVIDER_ID": ["101"],
            "SCORE": [1.5],
        })

        result = compare_detailed(py, sas, key="PROVIDER_ID")

        self.assertFalse(result["key_found"])

    def test_no_key_ignores_dataframe_index(self):
        sas = pd.DataFrame({"SCORE": [1.0, 2.0]})
        py = pd.DataFrame({"SCORE": [2.0, 1.0]}, index=[1, 0])

        result = compare_detailed(py, sas, key=None)

        self.assertFalse(result["passed"])
        self.assertEqual(result["mismatches"]["SCORE"]["count"], 2)

    def test_no_key_compares_real_row_position_column(self):
        sas = pd.DataFrame({
            "_ROW_POSITION_": [8.0, 9.0],
            "SCORE": [1.0, 2.0],
        })

        py = pd.DataFrame({
            "_ROW_POSITION_": [1.0, 2.0],
            "SCORE": [1.0, 2.0],
        })

        result = compare_detailed(py, sas, key=None)

        self.assertFalse(result["passed"])
        self.assertEqual(result["mismatches"]["_ROW_POSITION_"]["count"], 2)

    def test_no_key_mismatch_example_names_row_position(self):
        sas = pd.DataFrame({"SCORE": [1.0, 2.0]})
        py = pd.DataFrame({"SCORE": [1.0, 9.0]})

        result = compare_detailed(py, sas, key=None)

        self.assertEqual(
            result["mismatches"]["SCORE"]["examples"],
            [{
                "_ROW_POSITION_": "1",
                "SCORE_py": 9.0,
                "SCORE_sas": 2.0,
            }],
        )

    def test_no_key_leaves_input_tables_unchanged(self):
        sas = pd.DataFrame({"score": [1.0]})
        py = pd.DataFrame({"score": [1.0]})

        compare_detailed(py, sas, key=None)

        self.assertEqual(list(py.columns), ["score"])
        self.assertEqual(list(sas.columns), ["score"])

    def test_compare_report_omits_key_message_when_key_found(self):
        sas = pd.DataFrame({"SCORE": [1.5]})

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            compare(sas.copy(), sas, key=None)

        self.assertNotIn("Key column", output.getvalue())

    def test_compare_report_names_missing_key(self):
        sas = pd.DataFrame({"SCORE": [1.5]})

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            passed = compare(sas.copy(), sas, key="PROVIDER_ID")

        self.assertFalse(passed)
        self.assertIn("PROVIDER_ID", output.getvalue())

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
