"""Tests for generalized Program 0 and Program 2 orchestration."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from src.orchestrate import (
    OrchestrationConfigError,
    build_step_plan,
)

ROOT = Path(__file__).resolve().parents[1]


class StepPlanTests(unittest.TestCase):
    def test_program0_manifest(self):
        plan = build_step_plan(ROOT / "manifests/program0.json")

        self.assertEqual(34, len(plan))
        self.assertEqual("keep_hos_macro", plan[0]["id"])
        self.assertEqual("context", plan[1]["kind"])
        self.assertEqual("skip", plan[1]["action"])
        self.assertEqual(32, sum(s["kind"] == "step" for s in plan))

    def test_program2_manifest(self):
        plan = build_step_plan(ROOT / "manifests/program2.json")

        self.assertEqual(31, len(plan))
        self.assertEqual(5, sum(s["kind"] == "macro" for s in plan))
        self.assertEqual(26, sum(s["kind"] == "step" for s in plan))
        self.assertEqual("report_macro", plan[0]["id"])

    def test_rejects_forward_dependency(self):
        steps = [
            {
                "id": "consumer",
                "kind": "step",
                "translate": True,
                "depends_on": ["producer"],
            },
            {
                "id": "producer",
                "kind": "step",
                "translate": True,
                "depends_on": [],
            },
        ]

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            path.write_text(json.dumps(steps), encoding="utf-8")

            with self.assertRaises(OrchestrationConfigError):
                build_step_plan(path)


    def test_runtime_macro_producers_are_declared(self):
        pattern = re.compile(
            r"runtime \(set by (.+)\)",
            re.IGNORECASE,
        )

        for program in ("program0", "program2"):
            steps = json.loads(
                (
                    ROOT / "manifests" / f"{program}.json"
                ).read_text(encoding="utf-8")
            )

            by_id = {
                step["id"]: step
                for step in steps
            }

            for consumer in steps:
                for variable, description in consumer.get(
                    "macro_vars", {}
                ).items():
                    match = pattern.fullmatch(
                        str(description).strip()
                    )

                    if not match:
                        continue

                    producer_id = match.group(1)

                    # Variables supplied by another SAS program.
                    if producer_id.lower().endswith(".sas"):
                        continue

                    with self.subTest(
                        program=program,
                        consumer=consumer["id"],
                        variable=variable,
                        producer=producer_id,
                    ):
                        self.assertIn(
                            producer_id,
                            by_id,
                            "Unknown runtime-variable producer",
                        )

                        producer = by_id[producer_id]

                        self.assertEqual(
                            "runtime (set in this step)",
                            producer.get(
                                "macro_vars", {}
                            ).get(variable),
                        )

                        self.assertIn(
                            producer_id,
                            consumer.get("depends_on", []),
                        )


if __name__ == "__main__":
    unittest.main()
