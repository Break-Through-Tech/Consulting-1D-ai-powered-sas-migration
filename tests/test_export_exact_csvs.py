"""The exported CSVs hold exactly the same values as the SAS files."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src import data_io
from src.export_exact_csvs import export_exact_csvs, main

EXPECTED_FILES = {
    "alldata_2025jul.csv",
    "LESS100_MEASURE.csv",
    "MEASURE_AVERAGE_STDDEV_2025JUL.csv",
    "STD_DATA_2025JUL_ANALYSIS.csv",
    "OUTCOME_MORTALITY.csv",
    "OUTCOME_SAFETY.csv",
    "OUTCOME_READMISSION.csv",
    "PTEXP.csv",
    "PROCESS.csv",
    "STAR_2025JUL.csv",
    "NATIONAL_AVERAGE_2025JUL.csv",
}


def read_cells(path):
    # Parse numbers with float() so the check doesn't depend on read_csv's float parser.
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def assert_same_as_sas(path, sas):
    cells = read_cells(path)
    assert list(cells.columns) == list(sas.columns)
    assert len(cells) == len(sas)
    for column in sas.columns:
        if pd.api.types.is_numeric_dtype(sas[column]):
            values = cells[column].map(lambda text: float(text) if text else np.nan)
            np.testing.assert_array_equal(values.to_numpy(float), sas[column].to_numpy(float))
        else:
            assert cells[column].tolist() == sas[column].fillna("").tolist(), column


class ExportExactCsvsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp_dir.name) / "exact"
        cls.written = export_exact_csvs(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_writes_every_input_and_output_table(self):
        self.assertEqual({path.name for path in self.out.iterdir()}, EXPECTED_FILES)
        self.assertEqual({path.name for path in self.written}, EXPECTED_FILES)

    def test_output_values_match_sas_exactly(self):
        for name in data_io.OUTPUT_DATASETS:
            with self.subTest(name=name):
                assert_same_as_sas(self.out / f"{name.upper()}.csv", data_io.load_sas_output(name))

    def test_input_values_match_sas_exactly(self):
        assert_same_as_sas(self.out / "alldata_2025jul.csv", data_io.load_input())

    def test_values_keep_more_digits_than_the_rounded_csv(self):
        exact = read_cells(self.out / "OUTCOME_MORTALITY.csv")
        self.assertEqual(float(exact["Mean"].iloc[0]), -0.07348350342887125)

    def test_provider_id_keeps_leading_zeros(self):
        cells = read_cells(self.out / "OUTCOME_MORTALITY.csv")
        self.assertEqual(cells["PROVIDER_ID"].iloc[0], "010001")

    def test_empty_table_still_has_its_header(self):
        text = (self.out / "LESS100_MEASURE.csv").read_text().strip()
        self.assertEqual(text, "freq,measure_name")


class CommittedCsvTests(unittest.TestCase):
    def test_committed_output_csvs_match_sas_exactly(self):
        for name in data_io.OUTPUT_DATASETS:
            with self.subTest(name=name):
                assert_same_as_sas(data_io.SAS_OUTPUT_CSV_DIR / f"{name.upper()}.csv", data_io.load_sas_output(name))


class OverwriteGuardTests(unittest.TestCase):
    def test_existing_csv_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            original = out / "PTEXP.csv"
            original.write_text("keep me")

            with self.assertRaises(FileExistsError):
                export_exact_csvs(out)

            self.assertEqual(original.read_text(), "keep me")
            self.assertEqual([path.name for path in out.iterdir()], ["PTEXP.csv"])

    def test_command_line_reports_where_it_wrote(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "exact"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main([str(out)])

            self.assertIn("Wrote 11 CSV files", output.getvalue())
            self.assertIn(str(out), output.getvalue())


if __name__ == "__main__":
    unittest.main()
