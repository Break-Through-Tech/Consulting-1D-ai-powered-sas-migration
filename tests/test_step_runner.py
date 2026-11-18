"""Tests for the step runner and the step prompts (#61). No Gemini calls are made."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.orchestrate import build_step_plan, run_step_plan
from src.step_runner import StepExecutionError, StepRunner, macro_name
from src.translator import GeminiSASTranslator

ROOT = Path(__file__).resolve().parent.parent

MACRO_CODE = "def double(df):\n    out = df.copy()\n    out['x'] = out['x'] * 2\n    return out\n"


class FakeTranslator:
    """Returns canned code per step id and records what it was asked."""

    def __init__(self, code):
        self.code = code
        self.asked = []

    def translate_macro_definition(self, step, name):
        self.asked.append(("macro", step["id"], name))
        return self.code[step["id"]]

    def translate_step(self, step, macro_vars, macros):
        self.asked.append(("step", step["id"], dict(macro_vars), dict(macros)))
        return self.code[step["id"]]


def macro_step():
    return {"id": "double_macro", "kind": "macro", "action": "translate", "depends_on": [],
            "sas_code": "%macro double(indsn);\n  data &indsn; set &indsn; x = x*2; run;\n%mend;",
            "inputs": ["<indsn macro parameter>"], "outputs": ["<indsn macro parameter>"],
            "macro_vars": {"indsn": "macro parameter"}}


def data_step(step_id="call_double", inputs=("WORK.A",), outputs=("WORK.B",), macro_vars=None, depends=()):
    return {"id": step_id, "kind": "step", "action": "translate", "depends_on": list(depends),
            "sas_code": "%double(a);", "inputs": list(inputs), "outputs": list(outputs),
            "macro_vars": macro_vars or {}}


def approve(step, code):
    return True


class StepRunnerTests(unittest.TestCase):
    def run_plan(self, plan, code, review=approve, datasets=None):
        translator = FakeTranslator(code)
        runner = StepRunner(translator, review)
        result = run_step_plan(plan, runner, initial_datasets=datasets or {})
        return runner, translator, result

    def test_macro_definition_is_reused_by_a_later_call(self):
        plan = [macro_step(), data_step(depends=["double_macro"])]
        code = {
            "double_macro": MACRO_CODE,
            "call_double": "outputs = {'WORK.B': double(datasets['WORK.A'])}",
        }
        runner, translator, result = self.run_plan(plan, code, datasets={"WORK.A": pd.DataFrame({"x": [1, 2]})})

        self.assertEqual([2, 4], result["datasets"]["WORK.B"]["x"].tolist())
        self.assertIn("double", runner.macros)
        # The call step is told which macro functions exist, with their signatures.
        self.assertEqual({"double": "double(df)"}, translator.asked[-1][3])

    def test_runtime_macro_variable_is_returned_and_reaches_later_steps(self):
        plan = [
            data_step("producer", inputs=(), outputs=(), macro_vars={"n": "runtime (set in this step)"}),
            data_step("consumer", inputs=(), outputs=(), macro_vars={"n": "runtime (set by producer)"}),
        ]
        code = {"producer": "outputs = {}\nnew_macro_vars = {'n': 7}", "consumer": "outputs = {}"}
        _, translator, result = self.run_plan(plan, code)

        self.assertEqual(7, result["macro_vars"]["n"])
        self.assertEqual(7, translator.asked[-1][2]["n"])

    def test_sas_procs_is_in_scope(self):
        plan = [data_step("s", inputs=(), outputs=("WORK.P",))]
        code = {"s": "outputs = {'WORK.P': sas_procs.univariate_percentiles(\n"
                     "    pd.DataFrame({'v': [1, 2, 3, 4, 5]}), 'v', [20, 100])}"}
        _, _, result = self.run_plan(plan, code)

        self.assertEqual(["P20", "P100"], list(result["datasets"]["WORK.P"].columns))

    def test_code_that_is_not_approved_never_runs(self):
        plan = [data_step("s", inputs=(), outputs=())]
        code = {"s": "raise SystemExit('should not run')\noutputs = {}"}
        with self.assertRaisesRegex(StepExecutionError, "not approved"):
            self.run_plan(plan, code, review=lambda step, code: False)

    def test_review_must_answer_exactly_true(self):
        plan = [data_step("s", inputs=(), outputs=())]
        with self.assertRaisesRegex(StepExecutionError, "not approved"):
            self.run_plan(plan, {"s": "outputs = {}"}, review=lambda step, code: "yes")

    def test_missing_declared_output_is_reported(self):
        plan = [data_step("s", inputs=(), outputs=("WORK.B",))]
        with self.assertRaisesRegex(StepExecutionError, "missing"):
            self.run_plan(plan, {"s": "outputs = {}"})

    def test_output_must_be_a_dataframe(self):
        plan = [data_step("s", inputs=(), outputs=("WORK.B",))]
        with self.assertRaisesRegex(StepExecutionError, "not DataFrames"):
            self.run_plan(plan, {"s": "outputs = {'WORK.B': [1, 2]}"})

    def test_code_that_raises_names_the_step(self):
        plan = [data_step("s", inputs=(), outputs=())]
        with self.assertRaisesRegex(StepExecutionError, r"s: generated code failed: ZeroDivisionError"):
            self.run_plan(plan, {"s": "1 / 0"})

    def test_macro_code_must_define_the_named_function(self):
        with self.assertRaisesRegex(StepExecutionError, "'double'"):
            self.run_plan([macro_step()], {"double_macro": "def other(df):\n    return df\n"})

    def test_only_declared_outputs_leave_the_step(self):
        plan = [data_step("s", inputs=(), outputs=("WORK.B",))]
        code = {"s": "outputs = {'WORK.B': pd.DataFrame({'x': [1]}), 'WORK.EXTRA': pd.DataFrame()}"}
        _, _, result = self.run_plan(plan, code)

        self.assertEqual({"WORK.B"}, set(result["datasets"]))

    def test_a_step_cannot_change_an_earlier_dataset(self):
        original = pd.DataFrame({"x": [1, 2]})
        plan = [data_step("s", inputs=("WORK.A",), outputs=())]
        self.run_plan(plan, {"s": "datasets['WORK.A']['x'] = 0\noutputs = {}"}, datasets={"WORK.A": original})

        self.assertEqual([1, 2], original["x"].tolist())

    def test_reviewed_code_is_saved_when_a_directory_is_given(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = StepRunner(FakeTranslator({"s": "outputs = {}"}), approve, code_dir=directory)
            run_step_plan([data_step("s", inputs=(), outputs=())], runner)

            self.assertEqual("outputs = {}", (Path(directory) / "s.py").read_text(encoding="utf-8"))

    def test_review_callback_is_required(self):
        with self.assertRaises(TypeError):
            StepRunner(FakeTranslator({}), None)


class RealManifestTests(unittest.TestCase):
    def macro_names(self):
        names = []
        for program in (0, 2):
            plan = build_step_plan(ROOT / "manifests" / f"program{program}.json")
            names += [macro_name(step) for step in plan if step["kind"] == "macro"]
        return names

    def test_every_macro_step_in_programs_0_and_2_has_a_name(self):
        names = self.macro_names()

        self.assertIn("keep_hos", names)
        self.assertTrue({"report", "kmeans", "nation_avg0", "nation_avg_peer", "nation_avg"} <= set(names))

    def test_macro_step_names_are_unique_across_programs(self):
        names = self.macro_names()

        self.assertEqual(len(names), len(set(names)))


class PromptTests(unittest.TestCase):
    """The prompts reach Gemini with the right content; the client is a stand-in."""

    def translator(self):
        calls = []

        def generate_content(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=json.dumps({"python_code": "outputs = {}", "notes": ""}))

        translator = GeminiSASTranslator.__new__(GeminiSASTranslator)
        translator.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        translator.model_name = "test-model"
        return translator, calls

    def test_step_prompt_has_contract_plugins_and_step_details(self):
        translator, calls = self.translator()
        step = data_step("s", inputs=("WORK.A",), outputs=("WORK.B",), macro_vars={"n": "runtime (set in this step)"})
        code = translator.translate_step(step, {"year": "2025"}, {"double": "double(df)"})

        self.assertEqual("outputs = {}", code)
        system = calls[0]["config"].system_instruction
        user = calls[0]["contents"]
        self.assertIn("sas_procs.fastclus", system)
        self.assertIn("sas_procs.univariate_percentiles", system)
        self.assertIn("new_macro_vars", system)
        self.assertIn("WORK.A", user)
        self.assertIn("WORK.B", user)
        self.assertIn('"year": "2025"', user)
        self.assertIn("['n']", user)
        self.assertIn("double(df)", user)
        self.assertIn("%double(a);", user)

    def test_macro_definition_prompt_names_the_function_and_plugins(self):
        translator, calls = self.translator()
        translator.translate_macro_definition(macro_step(), "double")

        self.assertIn("named double", calls[0]["contents"])
        self.assertIn("%macro double", calls[0]["contents"])
        self.assertIn("sas_procs.fastclus", calls[0]["config"].system_instruction)


if __name__ == "__main__":
    unittest.main()
