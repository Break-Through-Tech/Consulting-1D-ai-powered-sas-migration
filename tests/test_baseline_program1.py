"""Program 1 baseline translation reproduces the SAS outputs exactly."""

import unittest

import numpy as np
import pandas as pd

from src import data_io
from src.baseline import program0, program1


def assert_matches_sas(df, name):
    # Compared against the .sas7bdat ground truth; the CSV exports are too rounded.
    pd.testing.assert_frame_equal(df, data_io.load_sas_output(name), check_dtype=False,
                                  check_exact=False, rtol=1e-9, atol=1e-9)


class Program1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p0 = program0.run(data_io.load_input())
        cls.result = program1.run(p0.std_data_analysis, p0.group_measures)

    def test_outputs_match_sas(self):
        self.assertEqual(sorted(self.result), sorted(program1.GROUP_OUTPUTS.values()))
        for name, df in self.result.items():
            with self.subTest(name=name):
                assert_matches_sas(df, name)


class GrpScoreTests(unittest.TestCase):
    def setUp(self):
        analysis = pd.DataFrame({"PROVIDER_ID": ["010001", "010005", "010006"],
                                 "std_A": [1.0, np.nan, 3.0],
                                 "std_B": [np.nan, np.nan, 5.0],
                                 "other": [9.0, 9.0, 9.0]})
        self.out = program1.grp_score(analysis, ["std_A", "std_B"])

    def test_columns(self):
        self.assertEqual(list(self.out.columns),
                         ["PROVIDER_ID", "std_A", "std_B", "C1", "C2", "total_cnt", "measure_wt",
                          "score_before_std", "Mean", "StdDev", "grp_score"])

    def test_values(self):
        out = self.out
        np.testing.assert_array_equal(out["C1"], [1, 0, 1])
        np.testing.assert_array_equal(out["C2"], [0, 0, 1])
        np.testing.assert_array_equal(out["total_cnt"], [1, 0, 2])
        # A hospital with no measures in the group gets no weight or score.
        np.testing.assert_array_equal(out["measure_wt"], [1, np.nan, 0.5])
        np.testing.assert_array_equal(out["score_before_std"], [1, np.nan, 4])
        self.assertTrue(out["Mean"].eq(2.5).all() and out["StdDev"].eq(np.sqrt(4.5)).all())
        np.testing.assert_allclose(out["grp_score"], [-np.sqrt(0.5), np.nan, np.sqrt(0.5)])


if __name__ == "__main__":
    unittest.main()
