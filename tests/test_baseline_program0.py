"""Program 0 baseline translation reproduces the SAS outputs exactly."""

import json
import unittest
from pathlib import Path

import pandas as pd

from src import data_io
from src.baseline import program0, program1

CONFIG = Path(__file__).resolve().parent.parent / "config" / "measure_groups.json"


def assert_matches_sas(df, name):
    # Compared against the .sas7bdat ground truth; the CSV exports are too rounded.
    pd.testing.assert_frame_equal(df, data_io.load_sas_output(name), check_dtype=False,
                                  check_exact=False, rtol=1e-9, atol=1e-9)


class Program0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = program0.run(data_io.load_input())

    def test_outputs_match_sas(self):
        for name, df in program0.outputs(self.result).items():
            with self.subTest(name=name):
                assert_matches_sas(df, name)

    def test_group_measures_match_config(self):
        config = json.loads(CONFIG.read_text())
        by_output = {group["output_table"].lower(): group["measures"] for group in config.values()}
        for group, measures in self.result.group_measures.items():
            with self.subTest(group=group):
                self.assertEqual(measures, by_output[program1.GROUP_OUTPUTS[group]])

    def test_every_included_measure_in_exactly_one_group(self):
        grouped = [m for ms in self.result.group_measures.values() for m in ms]
        self.assertEqual(sorted(grouped), sorted(f"std_{m}" for m in self.result.measure_in))


if __name__ == "__main__":
    unittest.main()
