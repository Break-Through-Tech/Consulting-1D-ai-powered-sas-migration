"""Test the generated-data handoffs and safeguards for Issue #32.

These tests use small sample datasets and mocked execution, so they can
check the pipeline logic without calling Gemini or running hospital data.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src import run_pipeline as chain
from src.orchestrate import load_measure_groups


class GeneratedPipelineHandoffTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)
        self.groups = load_measure_groups()

        self.input_data = pd.DataFrame({
            "PROVIDER_ID": ["000123"],
            "M": [1.0],
        })

        self.std_data = pd.DataFrame({
            "PROVIDER_ID": ["000123"],
            "M": [2.0],
        })

        self.p0_tables = {
            "LESS100_MEASURE": pd.DataFrame({"A": [1]}),
            "MEASURE_AVERAGE_STDDEV_2025JUL": pd.DataFrame({
                "A": [2],
            }),
            "STD_DATA_2025JUL_ANALYSIS": self.std_data,
        }

        self.p0_macros = {
            f"measure_{key}": " ".join(group["measures"])
            for key, group in self.groups.items()
        }

        self.p1_tables = {
            group["output_table"].upper(): pd.DataFrame({
                "PROVIDER_ID": ["000123"],
                **{
                    measure: [float(n)]
                    for n, measure in enumerate(group["measures"])
                },
            })
            for group in self.groups.values()
        }

        self.p2_tables = {
            "STAR_2025JUL": pd.DataFrame({
                "PROVIDER_ID": ["000123"],
                "STAR": [5],
            }),
            "NATIONAL_AVERAGE_2025JUL": pd.DataFrame({
                "AVG": [4.0],
            }),
        }

        self.p0_result = {
            "steps": [
                {"id": "p0", "status": "ok"},
            ],
            "datasets": {
                "HC.ALLDATA_2025JUL": self.input_data,
                **self.p0_tables,
            },
            "macro_vars": self.p0_macros,
        }

        self.p2_result = {
            "steps": [
                {"id": "p2", "status": "ok"},
            ],
            "datasets": {**self.p2_tables},
            "macro_vars": {},
        }

        self.code_dirs = {
            i: self.root / f"code_{i}"
            for i in (0, 1, 2)
        }

    def test_full_chain_passes_generated_outputs_to_the_next_program(self):

        def fake_run_program1(**kwargs):
            generated_input = pd.read_csv(
                kwargs["input_csv"],
                dtype={"PROVIDER_ID": "string"},
            )

            self.assertEqual(
                2.0,
                generated_input["M"].iloc[0],
            )

            self.assertEqual(
                "000123",
                generated_input["PROVIDER_ID"].iloc[0],
            )

            self.assertNotEqual(
                1.0,
                generated_input["M"].iloc[0],
            )

            for name, table in self.p1_tables.items():
                table.to_csv(
                    kwargs["output_dir"] / f"{name}.csv",
                    index=False,
                )

            return {
                "steps": [{
                    "id": "grp_score_macro",
                    "status": "COMPLETE",
                }],
                "outputs": {
                    name: {"status": "PASS"}
                    for name in self.p1_tables
                },
            }

        with (
            patch.object(chain, "_verify_saved_files"),
            patch.object(
                chain,
                "build_step_plan",
                return_value=[],
            ),
            patch.object(
                chain,
                "build_program1_plan",
                return_value=[],
            ),
            patch.object(
                chain.data_io,
                "load_input",
                return_value=self.input_data,
            ),
            patch.object(
                chain.data_io,
                "load_sas_output",
                side_effect=AssertionError("SAS fallback"),
            ),
            patch.object(
                chain,
                "run_steps_report",
                side_effect=[
                    self.p0_result,
                    self.p2_result,
                ],
            ) as run_steps,
            patch.object(
                chain,
                "run_reviewed_program1",
                side_effect=fake_run_program1,
            ),
            patch.object(
                chain.validation_report,
                "build_report",
                return_value={
                    "status": "PASS",
                    "counts": {"PASS": 10},
                    "outputs": {},
                },
            ) as report,
            patch.object(
                chain.validation_report,
                "save_report",
            ),
        ):
            result = chain.run_pipeline(
                code_dirs=self.code_dirs,
                run_dir=self.root / "run",
                reviewed=True,
            )

        program2_kwargs = run_steps.call_args_list[1].kwargs
        handoff = program2_kwargs["initial_datasets"]

        self.assertIs(
            handoff["STD_DATA_2025JUL_ANALYSIS"],
            self.std_data,
        )

        self.assertEqual(
            set(handoff),
            {"STD_DATA_2025JUL_ANALYSIS", *self.p1_tables},
        )

        for name, expected in self.p1_tables.items():
            pd.testing.assert_frame_equal(
                handoff[name],
                expected.astype({
                    "PROVIDER_ID": "string",
                }),
            )

        macros = program2_kwargs["initial_macro_vars"]

        for key, config in self.groups.items():
            self.assertEqual(
                macros[f"measure_{key}"],
                self.p0_macros[f"measure_{key}"],
            )

            self.assertEqual(
                macros[f"measure_{key}_cnt"],
                str(len(config["measures"])),
            )

        self.assertEqual(
            set(report.call_args.args[0]),
            {
                name.upper()
                for name in chain.data_io.OUTPUTS
            },
        )

        self.assertEqual(
            "PASS",
            result["report"]["status"],
        )

        self.assertTrue(
            (
                self.root
                / "run"
                / "handoff_summary.json"
            ).is_file()
        )

        self.assertTrue(
            (
                self.root
                / "run"
                / "data"
                / "STAR_2025JUL.csv"
            ).is_file()
        )

    def test_no_generated_code_runs_without_review(self):
        with patch.object(
            chain.data_io,
            "load_input",
            side_effect=AssertionError("must not load"),
        ):
            with self.assertRaisesRegex(
                chain.PipelineRunError,
                "reviewed",
            ):
                chain.run_pipeline(
                    code_dirs=self.code_dirs,
                    run_dir=self.root / "run",
                    reviewed=False,
                )

        self.assertFalse(
            (self.root / "run").exists()
        )

    def test_missing_program0_measure_macro_never_falls_back_to_config_or_sas(self):
        bad = dict(self.p0_macros)
        del bad["measure_OM"]

        with self.assertRaisesRegex(
            chain.PipelineRunError,
            "measure_OM",
        ):
            chain._program2_inputs(
                self.p0_tables,
                self.p1_tables,
                bad,
                self.groups,
            )

    def test_wrong_program1_group_columns_block_the_chain(self):
        bad = dict(self.p1_tables)

        bad["OUTCOME_MORTALITY"] = pd.DataFrame({
            "PROVIDER_ID": ["000123"],
            "std_unexpected": [1.0],
        })

        with self.assertRaisesRegex(
            chain.PipelineRunError,
            "do not match",
        ):
            chain._program2_inputs(
                self.p0_tables,
                bad,
                self.p0_macros,
                self.groups,
            )

    def test_missing_generated_output_is_not_replaced_by_a_sas_reference(self):
        bad = dict(self.p0_tables)
        del bad["STD_DATA_2025JUL_ANALYSIS"]

        with self.assertRaisesRegex(
            chain.PipelineRunError,
            "not fall back",
        ):
            chain._required_outputs(bad, 0)

    def test_failed_step_is_not_treated_as_a_successful_chain(self):
        with self.assertRaisesRegex(
            chain.PipelineRunError,
            "broken",
        ):
            chain._require_step_success(
                {
                    "steps": [{
                        "id": "broken",
                        "status": "failed",
                        "error": "missing input",
                    }],
                },
                0,
            )

    def _no_execution_patches(self):
        return (
            patch.object(chain, "_verify_saved_files"),
            patch.object(
                chain,
                "build_step_plan",
                return_value=[],
            ),
            patch.object(
                chain,
                "build_program1_plan",
                return_value=[],
            ),
        )

    def test_chain_stops_before_running_code_that_reads_sas_answers(self):
        self.code_dirs[1].mkdir()

        (
            self.code_dirs[1] / "grp_score_macro.py"
        ).write_text(
            "from src.baseline.program1 import grp_score\n",
            encoding="utf-8",
        )

        verify, plan, plan1 = self._no_execution_patches()

        with (
            verify,
            plan,
            plan1,
            patch.object(
                chain.data_io,
                "load_input",
                side_effect=AssertionError("code ran"),
            ),
        ):
            with self.assertRaisesRegex(
                chain.PipelineRunError,
                "grp_score_macro.py.*src.baseline",
            ):
                chain.run_pipeline(
                    code_dirs=self.code_dirs,
                    run_dir=self.root / "run",
                    reviewed=True,
                )

        self.assertFalse(
            (self.root / "run").exists()
        )

    def test_generated_code_that_only_uses_its_inputs_is_accepted(self):
        for folder in self.code_dirs.values():
            folder.mkdir()

            (folder / "step.py").write_text(
                "outputs = {'X': datasets['X'].copy()}\n",
                encoding="utf-8",
            )

        chain._check_generated_code_is_standalone(
            self.code_dirs
        )

    def test_program1_error_stops_the_chain_before_program2(self):

        def failing_program1(**kwargs):
            return {
                "steps": [{
                    "id": "os",
                    "status": "ERROR",
                }],
                "outputs": {},
            }

        verify, plan, plan1 = self._no_execution_patches()

        with (
            verify,
            plan,
            plan1,
            patch.object(
                chain.data_io,
                "load_input",
                return_value=self.input_data,
            ),
            patch.object(
                chain,
                "run_steps_report",
                side_effect=[self.p0_result],
            ) as run_steps,
            patch.object(
                chain,
                "run_reviewed_program1",
                side_effect=failing_program1,
            ),
        ):
            with self.assertRaisesRegex(
                chain.PipelineRunError,
                "Program 1.*os",
            ):
                chain.run_pipeline(
                    code_dirs=self.code_dirs,
                    run_dir=self.root / "run",
                    reviewed=True,
                )

        self.assertEqual(
            1,
            run_steps.call_count,
        )

    def test_missing_program1_csv_is_not_replaced(self):
        with self.assertRaisesRegex(
            chain.PipelineRunError,
            "no generated output",
        ):
            chain._program1_outputs(self.root)

    def test_program1_ids_keep_leading_zeros(self):
        for name in chain.PROGRAM_OUTPUTS[1]:
            pd.DataFrame({
                "PROVIDER_ID": [123],
                "x": [1.0],
            }).to_csv(
                self.root / f"{name}.csv",
                index=False,
            )

        for name, table in chain._program1_outputs(
            self.root
        ).items():
            with self.subTest(table=name):
                self.assertEqual(
                    "000123",
                    table["PROVIDER_ID"].iloc[0],
                )


if __name__ == "__main__":
    unittest.main()
