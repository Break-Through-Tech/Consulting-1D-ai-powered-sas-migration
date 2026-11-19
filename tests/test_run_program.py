"""Program 2 gets Program 0 and 1 results through src/run_program.py (#72)."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from src import data_io
from src.orchestrate import build_step_plan
from src.run_program import main, program2_inputs
from src.step_runner import macro_name, run_steps_report

ROOT = Path(__file__).resolve().parent.parent
PROGRAM0 = ROOT / "manifests" / "program0.json"
PROGRAM2 = ROOT / "manifests" / "program2.json"
GROUPS = ("OM", "OS", "OR", "PtExp", "Process")


def write_stub_code(plan, code_dir):
    """One file per translated step. Macros define a function; every step raises.

    The stubs never produce a table, so a step that runs shows up as "failed".
    A step that never got to run because an input or macro variable is missing
    shows up as "blocked" instead.
    """
    for step in plan:
        if step["action"] != "translate":
            continue
        if step["kind"] == "macro":
            code = f"def {macro_name(step)}(*args, **kwargs):\n    raise RuntimeError('stub')\n"
        else:
            code = "raise RuntimeError('stub')\n"
        (Path(code_dir) / f"{step['id']}.py").write_text(code, encoding="utf-8")


def external_inputs(manifest):
    steps = json.loads(Path(manifest).read_text(encoding="utf-8"))
    produced = {name.upper() for step in steps for name in step.get("outputs", [])}
    return {
        name.upper() for step in steps for name in step.get("inputs", [])
        if name.upper() not in produced and not name.startswith("<")
    }


class Program2InputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.datasets, cls.macro_vars = program2_inputs()

    def test_every_table_program_2_reads_from_outside_is_given(self):
        given = {name.upper() for name in self.datasets}
        self.assertEqual(external_inputs(PROGRAM2) - given, set())

    def test_tables_are_the_sas_outputs(self):
        for name, table in self.datasets.items():
            sas = data_io.load_sas_output(name.lower())
            self.assertEqual(table.shape, sas.shape, name)

    def test_measure_counts_are_strings_like_sas_macro_variables(self):
        counts = {group: self.macro_vars[f"measure_{group}_cnt"] for group in GROUPS}
        self.assertEqual(counts, {"OM": "7", "OS": "8", "OR": "11", "PtExp": "8", "Process": "12"})

    def test_handoff_values_match_the_sas_program_1_tables(self):
        # Each SAS group table keeps exactly its std_ measures, in order, so its
        # columns give both the list and the count that Program 1 used.
        tables = {
            "OM": "outcome_mortality", "OS": "outcome_safety", "OR": "outcome_readmission",
            "PtExp": "ptexp", "Process": "process",
        }
        for group, table in tables.items():
            sas = data_io.load_sas_output(table)
            measures = [column for column in sas.columns if column.lower().startswith("std_")]
            self.assertEqual(
                [name.lower() for name in self.macro_vars[f"measure_{group}"].split()],
                [name.lower() for name in measures], group,
            )
            self.assertEqual(self.macro_vars[f"measure_{group}_cnt"], str(len(measures)), group)

    def test_measure_lists_match_what_program_0_sets(self):
        # The reviewed Gemini code for Program 0 sets these with PROC SQL INTO.
        result = run_steps_report(
            build_step_plan(PROGRAM0), ROOT / "tests" / "fixtures" / "program0_generated",
            reviewed=True, initial_datasets={"HC.ALLDATA_2025JUL": data_io.load_input()},
        )
        for group in GROUPS:
            name = f"measure_{group}"
            self.assertEqual(self.macro_vars[name].split(), result["macro_vars"][name].split(), name)

    def test_every_runtime_macro_variable_of_program_2_is_set(self):
        steps = json.loads(PROGRAM2.read_text(encoding="utf-8"))
        runtime = {
            name for step in steps for name, value in (step.get("macro_vars") or {}).items()
            if isinstance(value, str) and value.strip().lower().startswith("runtime (")
            and "set in this step" not in value.lower()
        }
        self.assertTrue(runtime)
        self.assertEqual(runtime - set(self.macro_vars), set())


class Program2RunTests(unittest.TestCase):
    def test_no_step_waits_for_an_input_or_macro_variable(self):
        plan = build_step_plan(PROGRAM2)
        datasets, macro_vars = program2_inputs()
        with tempfile.TemporaryDirectory() as code_dir:
            write_stub_code(plan, code_dir)
            result = run_steps_report(
                plan, code_dir, reviewed=True,
                initial_datasets=datasets, initial_macro_vars=macro_vars,
            )
        call_report = next(entry for entry in result["steps"] if entry["id"] == "call_report")
        self.assertEqual(call_report["status"], "failed")
        self.assertIn("stub", call_report["error"])
        for entry in result["steps"]:
            self.assertFalse(
                [reason for reason in entry["blocked_by"] if reason.startswith("macro variable")],
                entry["id"],
            )

    def test_without_the_handoff_call_report_is_blocked(self):
        # Shows the test above would catch a missing handoff.
        plan = build_step_plan(PROGRAM2)
        with tempfile.TemporaryDirectory() as code_dir:
            write_stub_code(plan, code_dir)
            result = run_steps_report(plan, code_dir, reviewed=True)
        call_report = next(entry for entry in result["steps"] if entry["id"] == "call_report")
        self.assertEqual(call_report["status"], "blocked")

    def test_command_runs_program_2(self):
        plan = build_step_plan(PROGRAM2)
        with tempfile.TemporaryDirectory() as code_dir:
            write_stub_code(plan, code_dir)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["2", "--code-dir", code_dir, "--reviewed"])
        self.assertEqual(code, 1)  # stubs make no tables, so Program 2's outputs are not checked
        text = out.getvalue()
        self.assertIn("STAR_2025JUL", text)
        self.assertIn("FAILED   call_report", text)


if __name__ == "__main__":
    unittest.main()
