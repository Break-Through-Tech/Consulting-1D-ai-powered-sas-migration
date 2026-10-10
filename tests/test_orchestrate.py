import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.orchestrate import (
    OrchestrationConfigError,
    build_program1_plan,
    resolve_group_config,
)


ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "manifests" / "program1.json"
CONFIG_PATH = ROOT / "config" / "measure_groups.json"

EXPECTED_ORDER = [
    "ctx_libs",
    "grp_score_macro",
    "om",
    "os",
    "or",
    "ptexp",
    "process",
]

EXPECTED_GROUP_CONFIG_KEYS = {
    "om": "OM",
    "os": "OS",
    "or": "OR",
    "ptexp": "PtExp",
    "process": "Process",
}


class Program1PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_json(self, payload, name):
        path = self.temp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def load_real_manifest(self):
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def load_real_config(self):
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_plan_preserves_program1_order(self):
        plan = build_program1_plan()

        self.assertEqual(
            EXPECTED_ORDER,
            [step["id"] for step in plan],
        )

    def test_context_step_is_skipped(self):
        plan = build_program1_plan()
        context = plan[0]

        self.assertEqual("ctx_libs", context["id"])
        self.assertEqual("context", context["kind"])
        self.assertFalse(context["translate"])
        self.assertEqual([], context["depends_on"])
        self.assertEqual("skip", context["action"])

    def test_macro_is_translated_before_groups(self):
        plan = build_program1_plan()
        ids = [step["id"] for step in plan]
        macro_index = ids.index("grp_score_macro")

        for group_id in EXPECTED_GROUP_CONFIG_KEYS:
            self.assertLess(
                macro_index,
                ids.index(group_id),
            )

    def test_groups_depend_on_grp_score_macro(self):
        plan = build_program1_plan()

        for step in plan:
            if step["kind"] != "group":
                continue

            self.assertTrue(step["translate"])
            self.assertEqual(
                ["grp_score_macro"],
                step["depends_on"],
            )
            self.assertEqual("translate", step["action"])

    def test_only_context_step_is_skipped(self):
        plan = build_program1_plan()

        skipped = [
            step["id"]
            for step in plan
            if step["action"] == "skip"
        ]

        self.assertEqual(["ctx_libs"], skipped)

    def test_group_steps_resolve_to_config_by_output_table(self):
        plan = build_program1_plan()

        groups = {
            step["id"]: step
            for step in plan
            if step["kind"] == "group"
        }

        self.assertEqual(
            set(EXPECTED_GROUP_CONFIG_KEYS),
            set(groups),
        )

        for step_id, expected_key in EXPECTED_GROUP_CONFIG_KEYS.items():
            step = groups[step_id]

            self.assertEqual(
                expected_key,
                step["group_config_key"],
            )

            self.assertEqual(
                step["outputs"][0],
                step["group_config"]["output_table"],
            )

    def test_missing_orchestration_metadata_fails(self):
        manifest = [
            {
                "id": "ctx_libs",
                "kind": "context",
            }
        ]

        path = self.write_json(manifest, "missing_metadata.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_translate_must_be_boolean(self):
        manifest = [
            {
                "id": "ctx_libs",
                "kind": "context",
                "translate": "false",
                "depends_on": [],
            }
        ]

        path = self.write_json(manifest, "bad_translate.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_kind_must_be_known_value(self):
        manifest = [
            {
                "id": "ctx_libs",
                "kind": "gruop",
                "translate": False,
                "depends_on": [],
            }
        ]

        path = self.write_json(manifest, "bad_kind.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_duplicate_step_ids_fail(self):
        manifest = [
            {
                "id": "ctx_libs",
                "kind": "context",
                "translate": False,
                "depends_on": [],
            },
            {
                "id": "ctx_libs",
                "kind": "context",
                "translate": False,
                "depends_on": [],
            },
        ]

        path = self.write_json(manifest, "duplicate_ids.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_context_step_cannot_be_translated(self):
        manifest = [
            {
                "id": "ctx_libs",
                "kind": "context",
                "translate": True,
                "depends_on": [],
            }
        ]

        path = self.write_json(manifest, "translated_context.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_group_must_depend_on_grp_score_macro(self):
        manifest = [
            {
                "id": "ctx_libs",
                "kind": "context",
                "translate": False,
                "depends_on": [],
            },
            {
                "id": "om",
                "kind": "group",
                "translate": True,
                "depends_on": ["ctx_libs"],
            },
        ]

        path = self.write_json(manifest, "wrong_group_dependency.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_dependency_must_appear_before_step(self):
        manifest = [
            {
                "id": "om",
                "kind": "group",
                "translate": True,
                "depends_on": ["grp_score_macro"],
            },
            {
                "id": "grp_score_macro",
                "kind": "macro",
                "translate": True,
                "depends_on": [],
            },
        ]

        path = self.write_json(manifest, "wrong_dependency_order.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_dependency_names_must_be_strings(self):
        manifest = [
            {
                "id": "grp_score_macro",
                "kind": "macro",
                "translate": True,
                "depends_on": [123],
            }
        ]

        path = self.write_json(manifest, "bad_dependency_type.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_duplicate_dependencies_fail(self):
        manifest = [
            {
                "id": "grp_score_macro",
                "kind": "macro",
                "translate": True,
                "depends_on": [],
            },
            {
                "id": "om",
                "kind": "group",
                "translate": True,
                "depends_on": [
                    "grp_score_macro",
                    "grp_score_macro",
                ],
            },
        ]

        path = self.write_json(manifest, "duplicate_dependencies.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_group_config_requires_exactly_one_match(self):
        step = {
            "id": "om",
            "kind": "group",
            "outputs": ["OUTCOME_MORTALITY"],
        }

        no_match = {
            "OS": {
                "output_table": "OUTCOME_SAFETY",
                "measures": [],
            }
        }

        with self.assertRaises(OrchestrationConfigError):
            resolve_group_config(step, no_match)

        duplicate_match = {
            "OM": {
                "output_table": "OUTCOME_MORTALITY",
                "measures": [],
            },
            "OTHER": {
                "output_table": "OUTCOME_MORTALITY",
                "measures": [],
            },
        }

        with self.assertRaises(OrchestrationConfigError):
            resolve_group_config(step, duplicate_match)

    def test_missing_group_step_leaves_config_unclaimed(self):
        manifest = self.load_real_manifest()
        manifest = [
            step
            for step in manifest
            if step["id"] != "process"
        ]

        path = self.write_json(manifest, "missing_process.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(path)

    def test_extra_config_group_fails(self):
        config = deepcopy(self.load_real_config())

        config["EXTRA"] = {
            "label": "Extra",
            "measures": [],
            "output_table": "EXTRA_OUTPUT",
        }

        path = self.write_json(config, "extra_config.json")

        with self.assertRaises(OrchestrationConfigError):
            build_program1_plan(config_path=path)

    def test_two_steps_cannot_claim_same_group_config(self):
        manifest = self.load_real_manifest()

        om_step = next(
            step
            for step in manifest
            if step["id"] == "om"
        )

        om_copy = deepcopy(om_step)
        om_copy["id"] = "om_copy"

        manifest.append(om_copy)

        path = self.write_json(
            manifest,
            "duplicate_claim.json",
        )

        with self.assertRaisesRegex(
            OrchestrationConfigError,
            "claim the same",
        ):
            build_program1_plan(path)



if __name__ == "__main__":
    unittest.main()
