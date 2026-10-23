"""The SAS input and ground-truth outputs load with the shapes in SAS_Log.log."""

import unittest

import numpy as np

from src import data_io

EXPECTED_SHAPES = {
    "less100_measure": (0, 2),
    "measure_average_stddev_2025jul": (5, 49),
    "std_data_2025jul_analysis": (4566, 94),
    "outcome_mortality": (4566, 21),
    "outcome_safety": (4566, 23),
    "outcome_readmission": (4566, 29),
    "ptexp": (4566, 23),
    "process": (4566, 31),
    "star_2025jul": (4566, 27),
    "national_average_2025jul": (1, 9),
}


class DataIOTests(unittest.TestCase):
    def test_input_loads(self):
        df = data_io.load_input()
        self.assertEqual(df.shape, (4566, 93))
        self.assertTrue(df["PROVIDER_ID"].is_unique)
        self.assertTrue(df["PROVIDER_ID"].str.len().eq(6).all())  # leading zeros preserved

    def test_sas_outputs_match_log_and_csv(self):
        for name in data_io.OUTPUT_DATASETS:
            with self.subTest(name=name):
                sas = data_io.load_sas_output(name)
                csv = data_io.load_sas_output_csv(name)
                self.assertEqual(sas.shape, EXPECTED_SHAPES[name])
                self.assertEqual(list(sas.columns), list(csv.columns))

                # The CSV exports are rounded copies, off by up to ~5e-7.
                numeric = sas.select_dtypes("number").columns
                np.testing.assert_allclose(sas[numeric].to_numpy(float), csv[numeric].to_numpy(float),
                                           rtol=0, atol=1e-6, equal_nan=True)

    def test_sas_programs_readable(self):
        for filename in data_io.SAS_PROGRAMS:
            with self.subTest(filename=filename):
                self.assertTrue(data_io.read_sas_program(filename))


if __name__ == "__main__":
    unittest.main()
