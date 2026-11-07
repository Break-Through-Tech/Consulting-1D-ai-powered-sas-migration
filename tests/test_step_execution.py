"""Tests for macro-variable and dataset handoffs between SAS steps."""

import json
import unittest
from pathlib import Path

from src.orchestrate import OrchestrationConfigError, run_step_plan


def step(step_id, macro_vars=None, inputs=None):
    return {
        "id": step_id,
        "kind": "step",
        "action": "translate",
        "depends_on": [],
        "macro_vars": macro_vars or {},
        "inputs": inputs or [],
        "outputs": [],
    }


class StepExecutionTests(unittest.TestCase):
    def test_runtime_macro_value_reaches_later_step(self):
        plan = [
            {
                "id": "context",
                "kind": "context",
                "action": "skip",
                "macro_vars": {"year": "2025"},
                "depends_on": [],
            },
            step("producer", {"measure_in": "runtime (set in this step)"}),
            step("consumer", {
                "measure_in": "runtime (set by producer)",
            }),
        ]
        observed = []

        def execute(current, macros, datasets):
            observed.append((current["id"], dict(macros)))
            if current["id"] == "producer":
                return {"macro_vars": {"measure_in": "MORT_30_AMI"}}
            return {}

        result = run_step_plan(plan, execute)

        self.assertEqual(
            "MORT_30_AMI", observed[-1][1]["measure_in"]
        )
        self.assertEqual("2025", observed[-1][1]["year"])
        self.assertEqual(
            "MORT_30_AMI", result["macro_vars"]["measure_in"]
        )
        self.assertEqual(2, len(observed))

    def test_accepts_values_from_previous_program(self):
        plan = [step(
            "report",
            {"measure_OM_cnt": "runtime (set by Program 1)"},
            ["OUTCOME_MORTALITY"],
        )]

        def execute(current, macros, datasets):
            self.assertEqual(7, macros["measure_OM_cnt"])
            self.assertEqual("group scores", datasets["OUTCOME_MORTALITY"])
            return {}

        run_step_plan(
            plan,
            execute,
            initial_macro_vars={"measure_OM_cnt": 7},
            initial_datasets={"OUTCOME_MORTALITY": "group scores"},
        )

    def test_dataset_output_reaches_consumer(self):
        plan = [
            step("producer"),
            step("consumer", inputs=["WORK.ALL"]),
        ]

        def execute(current, macros, datasets):
            if current["id"] == "producer":
                return {"datasets": {"WORK.ALL": "hospital records"}}
            self.assertEqual("hospital records", datasets["WORK.ALL"])
            return {}

        result = run_step_plan(plan, execute)
        self.assertEqual(
            "hospital records", result["datasets"]["WORK.ALL"]
        )

    def test_rejects_unresolved_runtime_variable(self):
        plan = [step(
            "consumer", {"measure_cnt": "runtime (set by producer)"}
        )]

        with self.assertRaisesRegex(
            OrchestrationConfigError, "measure_cnt"
        ):
            run_step_plan(plan, lambda current, macros, datasets: {})

    def test_rejects_missing_input_dataset(self):
        plan = [step("consumer", inputs=["WORK.MISSING"])]

        with self.assertRaisesRegex(
            OrchestrationConfigError, "WORK.MISSING"
        ):
            run_step_plan(plan, lambda current, macros, datasets: {})


    def test_placeholders_are_never_passed_to_steps(self):
        plan = [
            step("producer", {
                "measure_in": "runtime (set in this step)",
                "year": "2025",
            }),
            step("consumer", {
                "measure_in": "runtime (set by producer)",
            }),
        ]
        seen = {}

        def execute(current, macros, datasets):
            seen[current["id"]] = dict(macros)
            if current["id"] == "producer":
                return {"macro_vars": {"measure_in": "MORT_30_AMI"}}
            return {}

        run_step_plan(plan, execute)

        self.assertEqual("2025", seen["producer"]["year"])
        self.assertNotIn("measure_in", seen["producer"])

        for step_id, macros in seen.items():
            for name, value in macros.items():
                with self.subTest(step=step_id, var=name):
                    self.assertFalse(
                        str(value).startswith("runtime (")
                    )

    def test_step_that_does_not_set_declared_variable_fails(self):
        plan = [
            step(
                "producer",
                {"measure_in": "runtime (set in this step)"},
            )
        ]

        with self.assertRaisesRegex(
            OrchestrationConfigError,
            "producer.*measure_in",
        ):
            run_step_plan(
                plan,
                lambda current, macros, datasets: {},
            )

    def test_rejects_misspelled_result_key(self):
        plan = [
            step("producer"),
            step("consumer", inputs=["WORK.ALL"]),
        ]

        def execute(current, macros, datasets):
            if current["id"] == "producer":
                return {
                    "dataset": {"WORK.ALL": "hospital records"}
                }
            return {}

        with self.assertRaisesRegex(
            OrchestrationConfigError,
            "producer.*dataset",
        ):
            run_step_plan(plan, execute)

    def test_step_that_does_not_produce_declared_dataset_fails(self):
        producer = step("producer")
        producer["outputs"] = ["WORK.ALL"]

        with self.assertRaisesRegex(
            OrchestrationConfigError,
            "producer.*WORK.ALL",
        ):
            run_step_plan(
                [producer],
                lambda current, macros, datasets: {},
            )


    def test_macro_definition_does_not_require_parameter_datasets(self):
        macro = {
            "id": "keep_hos_macro",
            "kind": "macro",
            "action": "translate",
            "depends_on": [],
            "macro_vars": {
                "indsn": "macro parameter",
            },
            "inputs": ["<indsn macro parameter>"],
            "outputs": ["<outdsn macro parameter>"],
        }

        consumer = step("consumer")
        consumer["depends_on"] = ["keep_hos_macro"]

        executed = []

        def execute(current, macros, datasets):
            executed.append(current["id"])
            self.assertNotIn(
                "<indsn macro parameter>",
                datasets,
            )
            return {}

        result = run_step_plan(
            [macro, consumer],
            execute,
        )

        self.assertEqual(
            ["keep_hos_macro", "consumer"],
            executed,
        )
        self.assertNotIn(
            "<outdsn macro parameter>",
            result["datasets"],
        )


    def test_real_program_manifest_handoffs(self):
        from src.orchestrate import build_step_plan

        root = Path(__file__).resolve().parents[1]

        expected_external = {
            "program0": {"HC.ALLDATA_2025JUL"},
            "program2": {
                "OUTCOME_MORTALITY",
                "OUTCOME_READMISSION",
                "OUTCOME_SAFETY",
                "PROCESS",
                "PTEXP",
                "STD_DATA_2025JUL_ANALYSIS",
            },
        }

        for program, expected in expected_external.items():
            with self.subTest(program=program):
                plan = build_step_plan(
                    root / "manifests" / f"{program}.json"
                )

                produced = set()
                external = set()

                for current in plan:
                    if (
                        current["kind"] == "macro"
                        or current["action"] == "skip"
                    ):
                        continue

                    external.update(
                        name
                        for name in current.get("inputs", [])
                        if name not in produced
                    )

                    produced.update(current.get("outputs", []))

                self.assertEqual(expected, external)

                initial_macros = {}

                if program == "program2":
                    initial_macros = {
                        f"measure_{group}_cnt": 7
                        for group in (
                            "OM", "OS", "OR",
                            "PtExp", "Process",
                        )
                    }

                executed = []

                def fake_execute(current, macros, datasets):
                    executed.append(current["id"])

                    for name, value in macros.items():
                        self.assertFalse(
                            isinstance(value, str)
                            and value.lower().startswith(
                                ("runtime (", "unknown (")
                            ),
                            f"{current['id']}: unresolved {name}",
                        )

                    return {
                        "macro_vars": {
                            name: (
                                0 if name == "NOBS"
                                else f"mock_{name}"
                            )
                            for name, description in current.get(
                                "macro_vars", {}
                            ).items()
                            if isinstance(description, str)
                            and description.lower()
                            == "runtime (set in this step)"
                        },
                        "datasets": {
                            name: f"mock_{name}"
                            for name in current.get("outputs", [])
                            if current["kind"] != "macro"
                        },
                    }

                result = run_step_plan(
                    plan,
                    fake_execute,
                    initial_macro_vars=initial_macros,
                    initial_datasets={
                        name: f"external_{name}"
                        for name in external
                    },
                )

                expected_callbacks = sum(
                    current["action"] != "skip"
                    for current in plan
                )

                self.assertEqual(
                    expected_callbacks,
                    len(executed),
                )

                for name in expected:
                    self.assertIn(name, result["datasets"])

                if program == "program0":
                    self.assertEqual(
                        0,
                        result["macro_vars"]["NOBS"],
                    )
                else:
                    for name, value in initial_macros.items():
                        self.assertEqual(
                            value,
                            result["macro_vars"][name],
                        )


    def test_program2_inputs_are_written_by_programs_0_and_1(self):
        root = Path(__file__).resolve().parents[1]
        written = set()

        for program in ("program0", "program1"):
            steps = json.loads(
                (
                    root / "manifests" / f"{program}.json"
                ).read_text(encoding="utf-8")
            )

            written.update(
                name
                for current in steps
                for name in current.get("outputs", [])
            )

        needed = {
            "OUTCOME_MORTALITY",
            "OUTCOME_READMISSION",
            "OUTCOME_SAFETY",
            "PROCESS",
            "PTEXP",
            "STD_DATA_2025JUL_ANALYSIS",
        }

        missing = needed - written

        self.assertEqual(
            set(),
            missing,
            f"Program 2 inputs not produced upstream: {sorted(missing)}",
        )


    def test_skipped_step_cannot_promise_runtime_variable(self):
        plan = [{
            "id": "context_setup",
            "kind": "context",
            "action": "skip",
            "depends_on": [],
            "macro_vars": {
                "run_date": "runtime (set in this step)"
            },
        }]

        with self.assertRaisesRegex(
            OrchestrationConfigError,
            "context_setup.*skipped.*run_date",
        ):
            run_step_plan(
                plan,
                lambda current, macros, datasets: {},
            )

    def test_rejects_placeholder_returned_as_value(self):
        plan = [step("producer")]

        with self.assertRaisesRegex(
            OrchestrationConfigError,
            "producer.*unresolved.*measure_in",
        ):
            run_step_plan(
                plan,
                lambda current, macros, datasets: {
                    "macro_vars": {
                        "measure_in": "runtime (set by producer)"
                    }
                },
            )


if __name__ == "__main__":
    unittest.main()
