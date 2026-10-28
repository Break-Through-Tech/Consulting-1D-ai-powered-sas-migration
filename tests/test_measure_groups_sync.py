"""config/measure_groups.json must match the measure lists Program 0 really builds.

Follow-up promised when PR #31 was merged. config/measure_groups.json was
written by hand from the SAS output CSV headers; src/baseline/program0.py
builds the same lists by running Program 0's logic on the real input data.
If CMS adds or drops a measure, this test catches the config going stale.
"""

import json
import unittest
from pathlib import Path

from src import data_io
from src.baseline import program0

ROOT = Path(__file__).resolve().parent.parent
# config key -> name Program 0 uses for the group
GROUP_NAMES = {"OM": "outcomes_mortality", "OS": "outcomes_safety",
               "OR": "outcomes_readmission", "PtExp": "ptexp", "Process": "process"}


class MeasureGroupsInSync(unittest.TestCase):
    def test_config_matches_program0(self):
        config = json.loads((ROOT / "config" / "measure_groups.json").read_text(encoding="utf-8"))
        built = program0.run(data_io.load_input()).group_measures
        for key, name in GROUP_NAMES.items():
            with self.subTest(group=key):
                self.assertEqual(config[key]["measures"], list(built[name]))


if __name__ == "__main__":
    unittest.main()
