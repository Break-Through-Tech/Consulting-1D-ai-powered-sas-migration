"""Tests for the step runner and the step prompts (#61). No Gemini calls are made."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.orchestrate import build_step_plan, run_step_plan
from src.step_runner import SasFrame, StepExecutionError, StepRunner, expand_name_list, macro_name
from src.translator import GeminiSASTranslator

ROOT = Path(__file__).resolve().parent.parent

MACRO_CODE = "def double(df):\n    out = df.copy()\n    out['x'] = out['x'] * 2\n    return out\n"


class FakeTranslator:
    """Returns canned code per step id and records what it was asked."""

    def __init__(self, code):
        self.code = code
        self.asked = []

    def translate_macro_definition(self, step, name, feedback=None):
        self.asked.append(("macro", step["id"], name))
        return self.code[step["id"]]

    def translate_step(self, step, macro_vars, macros, feedback=None):
        self.feedback = feedback
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

    def test_dataset_names_ignore_case_like_sas(self):
        plan = [data_step("s", inputs=("WORK.ALL_DATA_2025JUL",), outputs=("WORK.OUT_2025JUL",))]
        # Built from a macro variable, 'Jul' has a different case than the manifest's 'JUL'.
        code = {"s": "name = 'work.all_data_2025' + 'Jul'\n"
                     "outputs = {'WORK.Out_2025Jul': datasets[name]}"}
        _, _, result = self.run_plan(plan, code, datasets={"WORK.ALL_DATA_2025JUL": pd.DataFrame({"x": [1]})})

        self.assertEqual([1], result["datasets"]["WORK.OUT_2025JUL"]["x"].tolist())

    def test_review_callback_is_required(self):
        with self.assertRaises(TypeError):
            StepRunner(FakeTranslator({}), None)


class SasFrameTests(unittest.TestCase):
    """Column lookups ignore case, as in SAS (#61)."""

    def test_reading_a_column_under_another_case(self):
        frame = SasFrame(pd.DataFrame({"measure_name": ["A"], "COL1": [1]}))

        self.assertEqual(["A"], frame["measure_Name"].tolist())
        self.assertEqual([1], frame["col1"].tolist())

    def test_writing_a_column_under_another_case_replaces_it(self):
        frame = SasFrame(pd.DataFrame({"measure_name": ["A"]}))
        frame["Measure_Name"] = ["B"]

        self.assertEqual(["measure_name"], list(frame.columns))
        self.assertEqual(["B"], frame["measure_name"].tolist())

    def test_a_new_name_still_creates_a_column(self):
        frame = SasFrame(pd.DataFrame({"a": [1]}))
        frame["b"] = [2]

        self.assertEqual(["a", "b"], list(frame.columns))

    def test_two_columns_that_differ_only_by_case_stay_exact(self):
        frame = SasFrame(pd.DataFrame({"x": [1], "X": [2]}))

        self.assertEqual([2], frame["X"].tolist())
        with self.assertRaises(KeyError):
            frame["xX"]

    def test_a_missing_column_is_still_an_error(self):
        with self.assertRaises(KeyError):
            SasFrame(pd.DataFrame({"a": [1]}))["b"]

    def test_the_class_survives_filtering_and_copying(self):
        frame = SasFrame(pd.DataFrame({"COL1": [1, 200]}))
        filtered = frame[frame["col1"] > 100].copy()

        self.assertIsInstance(filtered, SasFrame)
        self.assertEqual([200], filtered["Col1"].tolist())

    def test_a_list_of_columns_under_other_cases(self):
        frame = SasFrame(pd.DataFrame({"PROVIDER_ID": ["1"], "grp_score": [2.0], "x": [3]}))
        picked = frame[["provider_id", "GRP_SCORE"]]

        self.assertEqual(["PROVIDER_ID", "grp_score"], list(picked.columns))
        self.assertIsInstance(picked, SasFrame)

    def test_a_list_may_mix_exact_and_other_case_names_and_masks_still_work(self):
        frame = SasFrame(pd.DataFrame({"A": [1, 2], "b": [3, 4]}))

        self.assertEqual(["A", "b"], list(frame[["A", "B"]].columns))
        self.assertEqual([2], frame[[False, True]]["A"].tolist())
        with self.assertRaises(KeyError):
            frame[["a", "zzz"]]

    def test_writing_a_list_of_columns_under_other_cases(self):
        frame = SasFrame(pd.DataFrame({"A": [1], "B": [2]}))
        frame[["a", "b"]] = [[10, 20]]

        self.assertEqual(["A", "B"], list(frame.columns))
        self.assertEqual([10], frame["A"].tolist())

    def test_sort_values_by_another_case(self):
        frame = SasFrame(pd.DataFrame({"A": [2, 1], "b": ["x", "y"]}))

        self.assertEqual([1, 2], frame.sort_values(by="a")["A"].tolist())
        self.assertEqual([1, 2], frame.sort_values("a", ascending=True)["A"].tolist())
        self.assertEqual([2, 1], frame.sort_values(by=["B", "a"], ascending=[True, True])["A"].tolist())

    def test_drop_columns_by_another_case(self):
        frame = SasFrame(pd.DataFrame({"A": [1], "b": [2], "c": [3]}))

        self.assertEqual(["c"], list(frame.drop(columns=["a", "B"]).columns))
        self.assertEqual(["b", "c"], list(frame.drop(columns="a").columns))
        self.assertEqual(["b", "c"], list(frame.drop("a", axis=1).columns))

    def test_groupby_by_another_case(self):
        frame = SasFrame(pd.DataFrame({"G": ["x", "x", "y"], "v": [1, 2, 3]}))

        self.assertEqual({"x": 3, "y": 3}, frame.groupby("g")["v"].sum().to_dict())
        self.assertEqual({("x",): 2, ("y",): 1}, {k: len(v) for k, v in frame.groupby(["g"])})

    def test_dropna_and_drop_duplicates_subset_by_another_case(self):
        frame = SasFrame(pd.DataFrame({"A": [1, None, 1], "B": [1, 2, 1]}))

        self.assertEqual(2, len(frame.dropna(subset=["a"])))
        self.assertEqual(2, len(frame.drop_duplicates(subset=["a", "b"])))

    def test_merge_on_a_name_spelled_differently_in_each_frame(self):
        left = SasFrame(pd.DataFrame({"PROVIDER_ID": ["1", "2"], "x": [1, 2]}))
        right = pd.DataFrame({"provider_id": ["1", "2"], "y": [3, 4]})

        merged = left.merge(right, on="provider_id", how="inner")

        self.assertEqual(["PROVIDER_ID", "x", "y"], list(merged.columns))
        self.assertEqual([3, 4], merged["y"].tolist())

    def test_merge_with_left_on_and_right_on_and_a_list_of_keys(self):
        left = SasFrame(pd.DataFrame({"A": [1, 2], "B": [1, 2], "x": [5, 6]}))
        right = pd.DataFrame({"a": [1, 2], "b": [1, 2], "y": [7, 8]})

        self.assertEqual(["A", "B", "x", "y"], list(left.merge(right, on=["a", "b"]).columns))
        other = pd.DataFrame({"K": [1, 2], "y": [7, 8]})
        self.assertEqual([7, 8], left.merge(other, left_on="a", right_on="k")["y"].tolist())

    def test_a_missing_name_is_still_an_error_everywhere(self):
        frame = SasFrame(pd.DataFrame({"A": [1]}))

        for call in (lambda: frame.sort_values(by="zzz"), lambda: frame.drop(columns=["zzz"]),
                     lambda: frame.groupby("zzz"), lambda: frame.merge(frame, on="zzz")):
            with self.assertRaises(KeyError):
                call()

    def test_two_columns_that_differ_only_by_case_stay_exact_in_a_list_and_sort(self):
        frame = SasFrame(pd.DataFrame({"x": [2, 1], "X": [9, 8]}))

        self.assertEqual([1, 2], frame.sort_values(by="x")["x"].tolist())
        self.assertEqual([9, 8], frame[["X"]]["X"].tolist())
        with self.assertRaises(KeyError):
            frame.sort_values(by="xX")

    def test_a_step_can_use_a_column_list_under_the_wrong_case(self):
        plan = [data_step("s", inputs=("WORK.A",), outputs=("WORK.B",))]
        code = {"s": "outputs = {'WORK.B': datasets['WORK.A'][['PROVIDER_ID', 'GRP_SCORE']].sort_values(by='grp_score')}"}
        runner = StepRunner(FakeTranslator(code), approve)
        result = run_step_plan(
            plan, runner,
            initial_datasets={"WORK.A": pd.DataFrame({"provider_id": ["a", "b"], "grp_score": [2.0, 1.0], "z": [0, 0]})},
        )

        self.assertEqual(["b", "a"], result["datasets"]["WORK.B"]["provider_id"].tolist())
        self.assertIs(type(result["datasets"]["WORK.B"]), pd.DataFrame)

    def test_a_step_can_read_columns_under_the_wrong_case_and_returns_plain_frames(self):
        plan = [data_step("s", inputs=("WORK.A",), outputs=("WORK.B",))]
        code = {"s": "outputs = {'WORK.B': datasets['WORK.A'][datasets['WORK.A']['COL1'] > 100]}"}
        runner = StepRunner(FakeTranslator(code), approve)
        result = run_step_plan(plan, runner, initial_datasets={"WORK.A": pd.DataFrame({"col1": [1, 200]})})

        self.assertEqual([200], result["datasets"]["WORK.B"]["col1"].tolist())
        self.assertIs(type(result["datasets"]["WORK.B"]), pd.DataFrame)


class ExpandNameListTests(unittest.TestCase):
    def test_plain_names_and_ranges(self):
        self.assertEqual(
            ["COMP_HIP_KNEE", "HAI_1", "HAI_2", "HAI_3", "PSI_90_SAFETY"],
            expand_name_list("COMP_HIP_KNEE HAI_1-HAI_3 PSI_90_SAFETY"),
        )

    def test_short_range_form_and_single_step_range(self):
        self.assertEqual(["H_1", "H_2", "H_3"], expand_name_list("H_1-3"))
        self.assertEqual(["A5"], expand_name_list("A5-A5"))

    def test_with_columns_names_take_the_spelling_of_the_columns(self):
        columns = ["PROVIDER_ID", "OP_18B", "HAI_1", "hai_2"]

        self.assertEqual(["OP_18B", "HAI_1", "hai_2"], expand_name_list("OP_18b HAI_1-HAI_2", columns))

    def test_a_name_with_no_column_is_returned_as_written(self):
        self.assertEqual(["X_1", "Y"], expand_name_list("X_1 Y", ["PROVIDER_ID"]))

    def test_columns_can_be_a_pandas_index(self):
        frame = pd.DataFrame({"OP_18B": [1]})

        self.assertEqual(["OP_18B"], expand_name_list("OP_18b", frame.columns))

    def test_the_membership_check_models_make_now_finds_the_column(self):
        frame = pd.DataFrame({"OP_18B": [1, None], "MORT": [1, 2]})
        counted = {n: int(frame[n].notna().sum()) for n in expand_name_list("OP_18b MORT", frame.columns) if n in frame.columns}

        self.assertEqual({"OP_18B": 1, "MORT": 2}, counted)

    def test_runs_of_whitespace_are_ignored(self):
        self.assertEqual(["A", "B"], expand_name_list("  A \n B  "))

    def test_unusable_ranges_are_rejected(self):
        for text in ("HAI_6-HAI_1", "HAI_1-OP_6", "A-B"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    expand_name_list(text)

    def test_the_real_measure_list_expands_to_plain_names(self):
        plan = build_step_plan(ROOT / "manifests" / "program0.json")
        measure_all = next(step["macro_vars"]["measure_all"] for step in plan if "measure_all" in step["macro_vars"])
        names = expand_name_list(measure_all)

        self.assertIn("HAI_1", names)
        self.assertIn("HAI_6", names)
        self.assertFalse([name for name in names if "-" in name])

    def test_it_is_in_the_scope_of_every_step(self):
        plan = [data_step("s", inputs=(), outputs=("WORK.N",))]
        code = {"s": "outputs = {'WORK.N': pd.DataFrame({'n': expand_name_list('X_1-X_3')})}"}
        _, _, result = self.run_plan_with(plan, code)

        self.assertEqual(["X_1", "X_2", "X_3"], result["datasets"]["WORK.N"]["n"].tolist())

    def run_plan_with(self, plan, code):
        translator = FakeTranslator(code)
        runner = StepRunner(translator, approve)
        return runner, translator, run_step_plan(plan, runner)


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
        self.assertIn("ignore case", system)
        self.assertIn("COL1", system)
        self.assertIn("HAI_1-HAI_6", system)
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
        self.assertIn("never pass a dataset name", calls[0]["config"].system_instruction)


if __name__ == "__main__":
    unittest.main()
