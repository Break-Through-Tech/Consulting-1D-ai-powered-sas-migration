"""The step runner keeps everything a plan hands from step to step (#74).

One small plan uses all three things the runner must carry: a dataset, a macro
variable and a macro function. The first test checks that a good run delivers
all three to the end. The others break one at a time and check the run fails.
"""

import unittest

import pandas as pd

from src.orchestrate import OrchestrationConfigError, run_step_plan
from src.step_runner import StepExecutionError, StepRunner
from tests.test_step_runner import FakeTranslator, approve, data_step, macro_step

MACRO_CODE = "def double(df):\n    out = df.copy()\n    out['x'] = out['x'] * 2\n    return out"

# make_n sets a macro variable, call_double uses a macro function, use_n reads the variable.
GOOD = {
    "double_macro": MACRO_CODE,
    "make_n": "outputs = {'WORK.N': datasets['WORK.A']}\nnew_macro_vars = {'n': 3}",
    "call_double": "outputs = {'WORK.B': double(datasets['WORK.N'])}",
    "use_n": "outputs = {'WORK.C': datasets['WORK.B'].assign(n=macro_vars['n'])}",
}


def plan():
    return [
        macro_step(),
        data_step("make_n", inputs=("WORK.A",), outputs=("WORK.N",),
                  macro_vars={"n": "runtime (set in this step)"}),
        data_step("call_double", inputs=("WORK.N",), outputs=("WORK.B",),
                  depends=["double_macro", "make_n"]),
        data_step("use_n", inputs=("WORK.B",), outputs=("WORK.C",),
                  macro_vars={"n": "runtime (set by make_n)"}, depends=["call_double", "make_n"]),
    ]


def run(code):
    runner = StepRunner(FakeTranslator(code), approve)
    result = run_step_plan(plan(), runner, initial_datasets={"WORK.A": pd.DataFrame({"x": [1, 2]})})
    return runner, result


class RunnerContractTests(unittest.TestCase):
    def test_a_good_run_delivers_datasets_macro_variables_and_macro_functions(self):
        runner, result = run(GOOD)

        self.assertEqual([2, 4], result["datasets"]["WORK.B"]["x"].tolist())
        self.assertEqual([3, 3], result["datasets"]["WORK.C"]["n"].tolist())
        self.assertEqual(3, result["macro_vars"]["n"])
        self.assertIn("double", runner.macros)

    def test_a_dropped_output_fails_the_run_at_that_step(self):
        with self.assertRaisesRegex(StepExecutionError, "make_n"):
            run({**GOOD, "make_n": "outputs = {}\nnew_macro_vars = {'n': 3}"})

    def test_a_dropped_macro_variable_fails_the_run_at_that_step(self):
        # run_step_plan itself notices the missing variable, so it raises its own error.
        with self.assertRaisesRegex(OrchestrationConfigError, "make_n.*not returned"):
            run({**GOOD, "make_n": "outputs = {'WORK.N': datasets['WORK.A']}"})

    def test_a_missing_macro_function_fails_the_run_at_that_step(self):
        with self.assertRaisesRegex(StepExecutionError, "double_macro"):
            run({**GOOD, "double_macro": "def twice(df):\n    return df"})

    def test_a_later_step_cannot_call_a_macro_that_was_never_defined(self):
        bad = {**GOOD, "call_double": "outputs = {'WORK.B': triple(datasets['WORK.N'])}"}
        with self.assertRaisesRegex(StepExecutionError, "call_double"):
            run(bad)


if __name__ == "__main__":
    unittest.main()
