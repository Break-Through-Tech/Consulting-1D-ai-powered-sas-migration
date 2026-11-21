"""Tests for hospital-level rating audits."""

import csv
import tempfile
import unittest
from pathlib import Path

from src.hospital_audit import (
    GROUP_TABLES,
    FINAL_GROUP_COLUMNS,
    audit_hospital,
)


ROOT = Path(__file__).resolve().parent.parent


class HospitalAuditTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name)
        (self.run / "data").mkdir()

        self.write_table(
            "STD_DATA_2025JUL_ANALYSIS",
            ["PROVIDER_ID", "std_demo"],
            [
                {"PROVIDER_ID": "010001", "std_demo": "0.25"},
                {"PROVIDER_ID": "010002", "std_demo": ""},
                {"PROVIDER_ID": "010003", "std_demo": "0.75"},
            ],
        )

        for table_name in GROUP_TABLES.values():
            self.write_table(
                table_name,
                ["PROVIDER_ID", "std_demo", "total_cnt", "grp_score"],
                [
                    {
                        "PROVIDER_ID": "010001",
                        "std_demo": "0.3",
                        "total_cnt": "1",
                        "grp_score": "0.3",
                    },
                    {
                        "PROVIDER_ID": "010002",
                        "std_demo": "",
                        "total_cnt": "0",
                        "grp_score": "",
                    },
                    {
                        "PROVIDER_ID": "010003",
                        "std_demo": "0.4",
                        "total_cnt": "1",
                        "grp_score": "0.4",
                    },
                ],
            )

        rows = []

        for provider, score, rating, peer, group_score in (
            ("010001", "0.17", "4", "3) # of groups=5   ", "0.3"),
            ("010002", "", "", "", ""),
            ("010003", "0.46", "5", "3) # of groups=5", "0.4"),
        ):
            row = {
                "PROVIDER_ID": provider,
                "summary_score": score,
                "star": rating,
                "cnt_grp": peer,
            }

            for column in FINAL_GROUP_COLUMNS.values():
                row[column] = group_score

            rows.append(row)

        self.write_table(
            "STAR_2025JUL",
            [
                "PROVIDER_ID",
                "summary_score",
                "star",
                "cnt_grp",
                *FINAL_GROUP_COLUMNS.values(),
            ],
            rows,
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_table(self, name, columns, rows):
        path = self.run / "data" / f"{name}.csv"

        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def test_hospital_id_and_rating(self):
        result = audit_hospital(self.run, "010001")

        self.assertEqual(result["provider_id"], "010001")
        self.assertEqual(result["star_rating"], 4)
        self.assertAlmostEqual(result["summary_score"], 0.17)
        self.assertEqual(len(result["groups"]), 5)

    def test_program0_and_program1_measure_values(self):
        result = audit_hospital(self.run, "010001")

        measure = result["groups"]["Mortality"]["measures"]["std_demo"]

        self.assertAlmostEqual(measure["program0"], 0.25)
        self.assertAlmostEqual(measure["program1"], 0.3)

    def test_missing_values_and_unrated_hospital(self):
        result = audit_hospital(self.run, "010002")

        self.assertIsNone(result["star_rating"])
        self.assertIsNone(result["summary_score"])
        self.assertIsNone(result["peer_group"])
        self.assertEqual(result["observed_rating_ranges"], [])

        self.assertIsNone(
            result["groups"]["Mortality"]["group_score"]
        )
        self.assertIsNone(
            result["groups"]["Mortality"]["measures"][
                "std_demo"
            ]["program0"]
        )

    def test_peer_group_trailing_spaces(self):
        result = audit_hospital(self.run, "010001")

        self.assertEqual(
            result["peer_group"],
            "3) # of groups=5",
        )

        ranges = result["observed_rating_ranges"]

        self.assertEqual(
            {entry["star"] for entry in ranges},
            {4, 5},
        )

        self.assertEqual(
            sum(entry["count"] for entry in ranges),
            2,
        )

    def test_unknown_provider_id(self):
        with self.assertRaisesRegex(
            ValueError, "not found"
        ):
            audit_hospital(self.run, "999999")

    def test_leading_zeroes_are_preserved(self):
        with self.assertRaisesRegex(
            ValueError, "not found"
        ):
            audit_hospital(self.run, "10001")

    def test_duplicate_provider_id_is_rejected(self):
        path = self.run / "data/STAR_2025JUL.csv"

        with path.open("r", newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
            columns = list(rows[0])

        rows.append(dict(rows[0]))

        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

        with self.assertRaisesRegex(
            ValueError, "Duplicate provider ID"
        ):
            audit_hospital(self.run, "010001")

    def test_missing_output_file(self):
        (self.run / "data/PROCESS.csv").unlink()

        with self.assertRaises(FileNotFoundError):
            audit_hospital(self.run, "010001")


class SavedHospitalRunTests(unittest.TestCase):

    def test_real_saved_pipeline_run(self):
        run = (
            ROOT
            / "output/chain_runs/20261120T224507Z_6ed62743"
        )

        if not (run / "data/STAR_2025JUL.csv").exists():
            self.skipTest("Local saved pipeline run unavailable")

        result = audit_hospital(run, "010001")

        self.assertEqual(result["provider_id"], "010001")
        self.assertEqual(result["star_rating"], 4)
        self.assertAlmostEqual(
            result["summary_score"],
            0.17162228425662915,
            places=10,
        )
        self.assertEqual(len(result["groups"]), 5)
        self.assertEqual(
            result["peer_group"],
            "3) # of groups=5",
        )


if __name__ == "__main__":
    unittest.main()
