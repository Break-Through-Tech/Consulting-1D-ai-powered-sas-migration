"""Tests for dependency tracing using synthetic and real step plans."""

import unittest
from pathlib import Path

from src.dependency_trace import (
    trace_output,
    trace_validation_report,
)
from src.orchestrate import build_step_plan, build_program1_plan


ROOT = Path(__file__).resolve().parent.parent


class DependencyTraceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.plans = {
            program: build_step_plan(
                ROOT / f"manifests/program{program}.json"
            )
            for program in (0, 2)
        }
        cls.plans[1] = build_program1_plan()

    def test_shared_upstream_step_affects_multiple_branches(self):
        plan = [
            {
                "id": "A",
                "depends_on": [],
                "inputs": [],
                "outputs": ["SOURCE"],
            },
            {
                "id": "B",
                "depends_on": ["A"],
                "inputs": ["SOURCE"],
                "outputs": ["FINAL"],
            },
            {
                "id": "C",
                "depends_on": ["A"],
                "inputs": ["SOURCE"],
                "outputs": ["OTHER"],
            },
        ]

        result = trace_output(plan, "FINAL")

        self.assertEqual("B", result["direct_writer"])
        self.assertEqual(["B", "A"], result["investigate_first"])

        self.assertNotIn("OTHER", result["downstream_outputs"])

        self.assertIn(
            "OTHER",
            result["candidate_downstream_impact"]["A"][
                "downstream_outputs"
            ],
        )

        self.assertNotIn(
            "OTHER",
            result["candidate_downstream_impact"]["B"][
                "downstream_outputs"
            ],
        )

    def test_last_writer_is_selected_for_overwritten_output(self):
        plan = [
            {
                "id": "first",
                "depends_on": [],
                "inputs": [],
                "outputs": ["RESULT"],
            },
            {
                "id": "second",
                "depends_on": ["first"],
                "inputs": ["RESULT"],
                "outputs": ["RESULT"],
            },
        ]

        result = trace_output(plan, "RESULT")

        self.assertEqual("second", result["direct_writer"])
        self.assertEqual(["first"], result["earlier_writers"])
        self.assertIn("first", result["all_candidate_steps"])

    def test_output_matching_is_case_insensitive(self):
        plan = [
            {
                "id": "make_result",
                "depends_on": [],
                "inputs": [],
                "outputs": ["STAR_2025JUL"],
            },
        ]

        result = trace_output(plan, "star_2025jul")

        self.assertEqual("make_result", result["direct_writer"])

    def test_unknown_output_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError, "No step produces output"
        ):
            trace_output(self.plans[0], "UNKNOWN_OUTPUT")

    def test_real_program0_manifest(self):
        result = trace_output(
            self.plans[0],
            "STD_DATA_2025JUL_ANALYSIS",
        )

        self.assertEqual(
            "data_std_data_2025jul_analysis",
            result["direct_writer"],
        )

        self.assertIn(
            "data_work_std_data_2025jul",
            result["investigate_first"],
        )

        self.assertIn(
            result["direct_writer"],
            result["all_candidate_steps"],
        )

    def test_real_program2_manifest(self):
        result = trace_output(
            self.plans[2],
            "NATIONAL_AVERAGE_2025JUL",
        )

        self.assertEqual(
            "data_national_average_2025jul_2",
            result["direct_writer"],
        )

        self.assertIn(
            "data_national_average_2025jul",
            result["investigate_first"],
        )

    def test_validation_report_routes_failures(self):
        report = {
            "outputs": {
                "STD_DATA_2025JUL_ANALYSIS": {
                    "program": 0,
                    "status": "FAIL",
                },
                "NATIONAL_AVERAGE_2025JUL": {
                    "program": 2,
                    "status": "FAIL",
                },
                "OUTCOME_SAFETY": {
                    "program": 1,
                    "status": "FAIL",
                },
                "LESS100_MEASURE": {
                    "program": 0,
                    "status": "PASS",
                },
            }
        }

        result = trace_validation_report(
            report, self.plans
        )

        self.assertIn(
            "STD_DATA_2025JUL_ANALYSIS",
            result["failed_outputs"],
        )

        self.assertIn(
            "NATIONAL_AVERAGE_2025JUL",
            result["failed_outputs"],
        )

        self.assertIn(
            "OUTCOME_SAFETY",
            result["failed_outputs"],
        )

        self.assertNotIn(
            "LESS100_MEASURE",
            result["failed_outputs"],
        )

    def test_real_program1_outputs(self):
        expected = {
            "OUTCOME_MORTALITY": "om",
            "OUTCOME_SAFETY": "os",
            "OUTCOME_READMISSION": "or",
            "PTEXP": "ptexp",
            "PROCESS": "process",
        }

        for output, writer in expected.items():
            with self.subTest(output=output):
                result = trace_output(self.plans[1], output)

                self.assertEqual(writer, result["direct_writer"])
                self.assertIn(
                    "grp_score_macro",
                    result["investigate_first"],
                )
                self.assertEqual(
                    2, len(result["all_candidate_steps"])
                )

    def test_earliest_failure_is_program1(self):
        report = {
            "outputs": {
                "STD_DATA_2025JUL_ANALYSIS": {
                    "program": 0, "status": "PASS"
                },
                "OUTCOME_MORTALITY": {
                    "program": 1, "status": "FAIL"
                },
                "OUTCOME_SAFETY": {
                    "program": 1, "status": "FAIL"
                },
                "STAR_2025JUL": {
                    "program": 2, "status": "FAIL"
                },
            }
        }

        result = trace_validation_report(report, self.plans)

        self.assertEqual(
            1, result["earliest_failing_stage"]
        )
        self.assertEqual(
            {"OUTCOME_MORTALITY", "OUTCOME_SAFETY"},
            set(result["earliest_failing_outputs"]),
        )
        self.assertIn(
            "OUTCOME_MORTALITY",
            result["failed_outputs"],
        )
        self.assertEqual(
            [{"program": 2, "output": "STAR_2025JUL"}],
            result["later_stage_failures"],
        )

    def test_program0_failure_precedes_later_failures(self):
        report = {
            "outputs": {
                "STD_DATA_2025JUL_ANALYSIS": {
                    "program": 0, "status": "FAIL"
                },
                "OUTCOME_MORTALITY": {
                    "program": 1, "status": "FAIL"
                },
                "STAR_2025JUL": {
                    "program": 2, "status": "FAIL"
                },
            }
        }

        result = trace_validation_report(report, self.plans)

        self.assertEqual(
            0, result["earliest_failing_stage"]
        )
        self.assertEqual(
            ["STD_DATA_2025JUL_ANALYSIS"],
            result["earliest_failing_outputs"],
        )
        self.assertEqual(
            {1, 2},
            {
                item["program"]
                for item in result["later_stage_failures"]
            },
        )

    def test_passing_report_has_no_traces(self):
        report = {
            "outputs": {
                "STD_DATA_2025JUL_ANALYSIS": {
                    "program": 0,
                    "status": "PASS",
                }
            }
        }

        result = trace_validation_report(
            report, self.plans
        )

        self.assertEqual({}, result["failed_outputs"])
        self.assertEqual({}, result["unsupported"])


if __name__ == "__main__":
    unittest.main()
