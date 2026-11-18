"""One report covers all 10 SAS outputs and says plainly what differs (#38)."""

import contextlib
import functools
import io
import json
import os
import re
import tempfile
import time
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from src import data_io, validation_report
from src.validate import DEFAULT_ATOL, DEFAULT_RTOL, compare_detailed
from src.validation_report import (
    JSON_NAME,
    SHOWN_COLUMNS,
    TEXT_NAME,
    baseline_tables,
    build_report,
    compare_output,
    csv_tables,
    format_report,
    main,
    problem_lines,
    save_report,
)

ROOT = Path(__file__).resolve().parent.parent

TEN_OUTPUTS = [
    "less100_measure", "measure_average_stddev_2025jul", "std_data_2025jul_analysis",
    "outcome_mortality", "outcome_safety", "outcome_readmission", "ptexp", "process",
    "star_2025jul", "national_average_2025jul",
]
PROGRAM0 = TEN_OUTPUTS[:3]

CANNOT_COMPARE = "The table could not be read or compared, so it is not known whether it matches SAS."
AS_TEXT_MEANS = ("A column is compared as text when it is not a number column in Python. "
                 "SAS holds numbers there, and text never equals a number.")
UNNAMED_HINT = ("UNNAMED means a column with no name, usually the row numbers pandas adds. "
                "Save the table with to_csv(index=False).")


@functools.lru_cache(maxsize=None)
def baseline():
    """The hand-written outputs, computed once. Copy a table before changing it."""
    return baseline_tables()


@functools.lru_cache(maxsize=None)
def baseline_report():
    return build_report(baseline(), "the hand-written Python")


@functools.lru_cache(maxsize=None)
def mixed_report():
    """2 outputs match, 2 differ, 1 cannot be compared and 5 have no table."""
    star = table("star_2025jul")
    star.loc[star["star"].first_valid_index(), "star"] += 1
    return build_report({
        "less100_measure": baseline()["less100_measure"],
        "measure_average_stddev_2025jul": table("measure_average_stddev_2025jul").iloc[:4],
        "std_data_2025jul_analysis": baseline()["std_data_2025jul_analysis"],
        "star_2025jul": star,
        "national_average_2025jul": None,
    }, "test")


def table(name):
    return baseline()[name].copy()


def report_for(name, changed):
    """The report entry for one changed table, and the whole report."""
    report = build_report({name: changed}, "test")
    return report["outputs"][name.upper()], report


def strict_loads(text):
    """json.loads that refuses the NaN and Infinity tokens strict JSON does not have."""
    def refuse(token):
        raise AssertionError(f"not strict JSON: {token}")
    return json.loads(text, parse_constant=refuse)


def run_command(*argv):
    """Run the command line. Returns (exit code, printed text)."""
    code, printed, _ = run_command_with_errors(*argv)
    return code, printed


def run_command_with_errors(*argv):
    """Run the command line. Returns (exit code, printed text, error text)."""
    printed, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
        try:
            code = main([str(arg) for arg in argv])
        except SystemExit as stop:
            code = stop.code
    return code, printed.getvalue(), errors.getvalue()


class RegistryTests(unittest.TestCase):
    def test_lists_the_ten_outputs_in_pipeline_order(self):
        self.assertEqual(list(data_io.OUTPUTS), TEN_OUTPUTS)
        self.assertEqual(data_io.OUTPUT_DATASETS, TEN_OUTPUTS)
        self.assertEqual([facts["program"] for facts in data_io.OUTPUTS.values()],
                         [0, 0, 0, 1, 1, 1, 1, 1, 2, 2])

    def test_every_key_identifies_exactly_one_row_in_sas(self):
        for name, facts in data_io.OUTPUTS.items():
            with self.subTest(name=name):
                sas = data_io.load_sas_output(name)
                if facts["key"] is None:
                    # Comparing by position is only unambiguous for a single row.
                    self.assertEqual(len(sas), 1)
                    continue
                # The validator finds the key without regard to upper or lower case.
                column = next(column for column in sas.columns
                              if column.strip().upper() == facts["key"].strip().upper())
                values = sas[column].astype("string").str.strip()
                self.assertTrue(values.is_unique)
                self.assertFalse((values.isna() | (values == "")).any())

    def test_only_the_national_averages_have_no_key(self):
        self.assertEqual([name for name, facts in data_io.OUTPUTS.items() if facts["key"] is None],
                         ["national_average_2025jul"])

    def test_names_are_the_permanent_outputs_in_the_manifests(self):
        # run_step_plan returns tables under the manifest names, so they must be these.
        for program in (0, 1, 2):
            with self.subTest(program=program):
                steps = json.loads((ROOT / "manifests" / f"program{program}.json").read_text(encoding="utf-8"))
                permanent = []
                for step in steps:
                    for name in step["outputs"]:
                        # WORK.X is a temporary table and <...> is a macro parameter.
                        if "." not in name and not name.startswith("<") and name not in permanent:
                            permanent.append(name)
                expected = [name.upper() for name, facts in data_io.OUTPUTS.items()
                            if facts["program"] == program]
                self.assertEqual(permanent, expected)

    def test_program1_outputs_are_all_matched_by_provider_id(self):
        # run_program1 passes PROVIDER_ID for these five itself, so the registry must say the same.
        keys = {facts["key"] for facts in data_io.OUTPUTS.values() if facts["program"] == 1}
        self.assertEqual(keys, {"PROVIDER_ID"})


class AllTenOutputsTests(unittest.TestCase):
    def test_hand_written_python_passes_all_ten(self):
        report = baseline_report()

        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["passed"])
        self.assertEqual(report["counts"], {"PASS": 10, "FAIL": 0, "ERROR": 0, "NOT_CHECKED": 0})
        self.assertEqual(list(report["outputs"]), [name.upper() for name in TEN_OUTPUTS])
        for name, entry in report["outputs"].items():
            with self.subTest(name=name):
                self.assertEqual(entry["status"], "PASS")
                self.assertTrue(entry["validation"]["passed"])

    def test_entries_say_the_program_and_the_key_as_the_validator_spells_it(self):
        outputs = baseline_report()["outputs"]

        self.assertEqual([entry["program"] for entry in outputs.values()], [0, 0, 0, 1, 1, 1, 1, 1, 2, 2])
        self.assertEqual(outputs["LESS100_MEASURE"]["key"], "MEASURE_NAME")
        self.assertEqual(outputs["MEASURE_AVERAGE_STDDEV_2025JUL"]["key"], "_STAT_")
        self.assertEqual(outputs["STAR_2025JUL"]["key"], "PROVIDER_ID")
        self.assertIsNone(outputs["NATIONAL_AVERAGE_2025JUL"]["key"])

    def test_entries_say_which_sas_columns_hold_text(self):
        # Every other SAS column holds numbers. The text report needs this to tell a column
        # that Python stores the wrong way from one that is text in SAS too.
        outputs = baseline_report()["outputs"]

        self.assertEqual(outputs["STAR_2025JUL"]["sas_text_columns"], ["PROVIDER_ID", "CNT_GRP"])
        self.assertEqual(outputs["MEASURE_AVERAGE_STDDEV_2025JUL"]["sas_text_columns"], ["_STAT_"])
        self.assertEqual(outputs["LESS100_MEASURE"]["sas_text_columns"], ["MEASURE_NAME"])
        self.assertEqual(outputs["NATIONAL_AVERAGE_2025JUL"]["sas_text_columns"], [])

    def test_largest_difference_is_tiny_and_empty_for_the_empty_table(self):
        outputs = baseline_report()["outputs"]

        # LESS100_MEASURE has no rows in SAS, so no number was compared.
        self.assertIsNone(outputs["LESS100_MEASURE"]["largest_difference"])
        for name in TEN_OUTPUTS[1:]:
            with self.subTest(name=name):
                self.assertLess(outputs[name.upper()]["largest_difference"], 1e-9)

    def test_largest_difference_is_the_biggest_gap_of_any_column_and_a_fail_has_it_too(self):
        # The demo (#44) and the evaluation (#43) read this number from the JSON.
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 0.25
        national.iloc[0, 1] -= 0.5

        entry, _ = report_for("national_average_2025jul", national)

        self.assertEqual(entry["status"], "FAIL")
        self.assertAlmostEqual(entry["largest_difference"], 0.5, places=9)

    def test_tolerance_is_the_validators_and_cannot_be_changed_here(self):
        report = baseline_report()

        self.assertEqual((report["rtol"], report["atol"]), (DEFAULT_RTOL, DEFAULT_ATOL))
        with self.assertRaises(TypeError):
            build_report({}, "test", rtol=1e-3)

    def test_a_gap_just_outside_the_tolerance_fails_and_just_inside_passes(self):
        # Summary_Score_Nat is about -0.034. A gap 10 times the tolerance must fail and one
        # a tenth of it must pass, so nothing on the way to the validator loosens it (#40).
        name = "national_average_2025jul"
        value = table(name).iloc[0, 0]
        outside, inside = table(name), table(name)
        outside.iloc[0, 0] = value * (1 + 1e-8)
        inside.iloc[0, 0] = value * (1 + 1e-10)

        self.assertEqual(report_for(name, outside)[0]["status"], "FAIL")
        self.assertEqual(report_for(name, inside)[0]["status"], "PASS")

    def test_a_value_that_is_zero_in_sas_is_held_to_the_absolute_tolerance(self):
        # Pt_Exp_Grp_Score_Nat is 0 in SAS apart from float noise, so only atol=1e-12 decides.
        name = "national_average_2025jul"
        outside, inside = table(name), table(name)
        outside.loc[0, "Pt_Exp_Grp_Score_Nat"] += 1e-11
        inside.loc[0, "Pt_Exp_Grp_Score_Nat"] += 1e-13

        self.assertEqual(report_for(name, outside)[0]["status"], "FAIL")
        self.assertEqual(report_for(name, inside)[0]["status"], "PASS")

    def test_validation_is_the_validators_own_result(self):
        # The feedback loop (#66) and the demo (#44) read these keys, so they are not renamed.
        name = "national_average_2025jul"
        direct = compare_detailed(table(name), data_io.load_sas_output(name), None)

        self.assertEqual(baseline_report()["outputs"][name.upper()]["validation"], direct)

    def test_report_says_where_the_tables_came_from_and_when(self):
        report = baseline_report()

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["source"], "the hand-written Python")
        self.assertEqual(report["programs"], [0, 1, 2])
        # UTC to the second, as the docs show it.
        self.assertRegex(report["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        # A folder given as the label is stored as text, which JSON can hold.
        self.assertEqual(build_report({}, Path("run_7"))["source"], "run_7")

    def test_report_is_strict_json(self):
        report = baseline_report()

        self.assertEqual(strict_loads(json.dumps(report, allow_nan=False)), report)

    def test_names_ignore_case_and_other_tables_are_ignored(self):
        tables = {name.upper(): frame for name, frame in baseline().items() if name in PROGRAM0}
        # Program 0 has a temporary table WORK.PTEXP. It is not Program 1's PTEXP.
        tables["WORK.PTEXP"] = table("ptexp")
        tables["HC.ALLDATA_2025JUL"] = "not a table"
        # Generated code decides what run_step_plan returns, so a name may not even be text.
        tables[0] = table("ptexp")

        report = build_report(tables, "test")

        self.assertEqual(report["counts"], {"PASS": 3, "FAIL": 0, "ERROR": 0, "NOT_CHECKED": 7})
        self.assertEqual(report["outputs"]["PTEXP"]["status"], "NOT_CHECKED")

    def test_two_names_differing_only_by_case_are_rejected(self):
        with self.assertRaises(ValueError):
            build_report({"STAR_2025JUL": table("star_2025jul"), "star_2025jul": table("star_2025jul")}, "test")

    def test_one_table_given_in_place_of_the_named_tables_is_refused(self):
        # A DataFrame has .items() too, so without this it would read as "no table was given".
        for wrong in (table("national_average_2025jul"), [table("national_average_2025jul")], "output/data"):
            with self.subTest(type=type(wrong).__name__):
                with self.assertRaises(TypeError) as caught:
                    build_report(wrong, "test")

                self.assertIn("compare_output", str(caught.exception))

    def test_a_mapping_that_is_not_a_dict_is_accepted(self):
        tables = types.MappingProxyType({"national_average_2025jul": table("national_average_2025jul")})

        self.assertEqual(build_report(tables, "test")["counts"]["PASS"], 1)


class PartialRunTests(unittest.TestCase):
    def test_only_program0_is_incomplete_not_pass(self):
        report = build_report({name: baseline()[name] for name in PROGRAM0}, "test")

        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertFalse(report["passed"])
        self.assertEqual(report["counts"], {"PASS": 3, "FAIL": 0, "ERROR": 0, "NOT_CHECKED": 7})
        # All 10 are always listed, so a reader can draw the same table for any run.
        self.assertEqual(len(report["outputs"]), 10)
        self.assertEqual(report["outputs"]["STAR_2025JUL"],
                         {"program": 2, "key": "PROVIDER_ID", "status": "NOT_CHECKED"})

    def test_one_output_without_a_table_is_enough_to_be_incomplete(self):
        nine = {name: frame for name, frame in baseline().items() if name != "national_average_2025jul"}

        report = build_report(nine, "test")

        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertFalse(report["passed"])
        self.assertEqual(report["counts"], {"PASS": 9, "FAIL": 0, "ERROR": 0, "NOT_CHECKED": 1})

    def test_nothing_given_is_never_a_pass(self):
        report = build_report({}, "test")

        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertFalse(report["passed"])
        self.assertEqual(report["counts"]["NOT_CHECKED"], 10)

    def test_a_failure_outweighs_outputs_that_were_not_checked(self):
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 1

        _, report = report_for("national_average_2025jul", national)

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["counts"], {"PASS": 0, "FAIL": 1, "ERROR": 0, "NOT_CHECKED": 9})

    def test_programs_keeps_input_tables_from_counting_as_matches(self):
        # A Program 2 run is given the earlier tables as inputs, and run_step_plan
        # returns them with its results. SAS tables would match themselves.
        seeded = {name: data_io.load_sas_output(name) for name in TEN_OUTPUTS[:8]}
        seeded.update({name: baseline()[name] for name in TEN_OUTPUTS[8:]})

        report = build_report(seeded, "test", programs=[2])

        self.assertEqual(report["programs"], [2])
        self.assertEqual(report["counts"], {"PASS": 2, "FAIL": 0, "ERROR": 0, "NOT_CHECKED": 8})
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertNotIn("validation", report["outputs"]["OUTCOME_MORTALITY"])
        self.assertEqual(report["outputs"]["STAR_2025JUL"]["status"], "PASS")

    def test_programs_can_be_one_number_or_several_in_any_order(self):
        self.assertEqual(build_report({}, "test", programs=2)["programs"], [2])
        self.assertEqual(build_report({}, "test", programs=(2, 0, 2))["programs"], [0, 2])

    def test_programs_are_stored_as_plain_ints(self):
        # A number taken from a table is a numpy number, which JSON cannot hold.
        for programs in (np.int64(2), [np.int64(2)]):
            with self.subTest(programs=programs):
                report = build_report({}, "test", programs=programs)

                self.assertEqual([type(program) for program in report["programs"]], [int])
                self.assertEqual(strict_loads(json.dumps(report, allow_nan=False))["programs"], [2])

    def test_programs_must_name_real_programs(self):
        for programs in ([], [3], ["0"], [0, 5], "2", 2.5, True, False, [True], [False], np.True_, [np.False_]):
            with self.subTest(programs=programs):
                with self.assertRaises(ValueError):
                    build_report(baseline(), "test", programs=programs)

        with self.assertRaisesRegex(ValueError, r"^programs must be one or more of \[0, 1, 2\], got \[3\]\.$"):
            build_report({}, "test", programs=[3])


class FailureTests(unittest.TestCase):
    def test_one_wrong_value_fails_that_output_and_names_the_row(self):
        star = table("star_2025jul")
        row = star["star"].first_valid_index()
        star.loc[row, "star"] += 1

        entry, report = report_for("star_2025jul", star)

        self.assertEqual(entry["status"], "FAIL")
        self.assertEqual(list(entry["validation"]["mismatches"]), ["STAR"])
        mismatch = entry["validation"]["mismatches"]["STAR"]
        self.assertEqual(mismatch["count"], 1)
        self.assertEqual(mismatch["examples"][0]["PROVIDER_ID"], star.loc[row, "PROVIDER_ID"])
        self.assertEqual(report["status"], "FAIL")

    def test_missing_and_infinite_values_stay_strict_json(self):
        name = "measure_average_stddev_2025jul"
        stats = table(name)
        column = stats.columns[3]
        stats.loc[stats["_STAT_"] == "MIN", column] = -np.inf
        stats.loc[stats["_STAT_"] == "MEAN", column] = np.nan
        stats.loc[stats["_STAT_"] == "STD", column] = np.inf

        entry, report = report_for(name, stats)

        mismatch = entry["validation"]["mismatches"][column.upper()]
        self.assertEqual((mismatch["count"], mismatch["python_missing"], mismatch["sas_missing"]), (3, 1, 0))
        python_values = {example["_STAT_"]: example[f"{column.upper()}_py"] for example in mismatch["examples"]}
        self.assertEqual(python_values, {"MIN": "-inf", "MEAN": None, "STD": "inf"})
        self.assertEqual(strict_loads(json.dumps(report, allow_nan=False)), report)

    def test_text_names_the_output_column_row_and_both_values(self):
        star = table("star_2025jul")
        row = star["star"].first_valid_index()
        sas_star = float(star.loc[row, "star"])
        star.loc[row, "star"] = sas_star + 1

        entry, report = report_for("star_2025jul", star)
        text = format_report(report)

        self.assertIn("RESULT: FAIL - 0 of 10 outputs match SAS, 1 differs, 9 not checked", text)
        self.assertIn("WHAT DIFFERS", text)
        self.assertIn("STAR_2025JUL (Program 2): FAIL", text)
        self.assertIn("Values differ in 1 column:", text)
        self.assertIn("- STAR: 1 row differs.", text)
        self.assertIn(f"First difference: PROVIDER_ID {star.loc[row, 'PROVIDER_ID']}: "
                      f"Python {sas_star + 1!r}, SAS {sas_star!r}", text)

    def test_text_shows_missing_and_infinite_values_in_words(self):
        name = "measure_average_stddev_2025jul"
        stats = table(name)
        column, other = stats.columns[3], stats.columns[4]
        stats.loc[stats["_STAT_"] == "MEAN", column] = np.nan
        stats.loc[stats["_STAT_"] == "STD", column] = np.inf
        # Only the first differing row of a column is shown, so minus infinity gets its own column.
        stats.loc[stats["_STAT_"] == "STD", other] = -np.inf

        lines = problem_lines(report_for(name, stats)[0])

        self.assertIn(f"- {column.upper()}: 2 rows differ (1 where Python has no value and SAS has one).", lines)
        self.assertTrue(any("_STAT_ MEAN: Python (missing), SAS " in line for line in lines), lines)
        self.assertTrue(any("_STAT_ STD: Python -infinity, SAS " in line for line in lines), lines)

    def test_text_says_which_side_has_no_value(self):
        # A number in Python where SAS has none usually means a SAS missing-value rule was
        # not followed, so the text must name the right side.
        sas = data_io.load_sas_output("star_2025jul")
        no_star_in_sas = sas.loc[sas["star"].isna(), "PROVIDER_ID"].iloc[0]
        star_in_sas = sas.loc[sas["star"].notna(), "PROVIDER_ID"].iloc[0]
        star = table("star_2025jul")
        star.loc[star["PROVIDER_ID"] == no_star_in_sas, "star"] = 3.0

        lines = problem_lines(report_for("star_2025jul", star)[0])

        self.assertIn("- STAR: 1 row differs (1 where SAS has no value and Python has one).", lines)
        self.assertIn(f"    First difference: PROVIDER_ID {no_star_in_sas}: Python 3.0, SAS (missing)", lines)

        star.loc[star["PROVIDER_ID"] == star_in_sas, "star"] = np.nan
        lines = problem_lines(report_for("star_2025jul", star)[0])

        self.assertIn("- STAR: 2 rows differ (1 where Python has no value and SAS has one; "
                      "1 where SAS has no value and Python has one).", lines)

    def test_a_missing_text_value_is_null_in_the_json_and_missing_in_the_text(self):
        star = table("star_2025jul")
        row = star.index[star["cnt_grp"] == "3) # of groups=5"][0]
        star.loc[row, "cnt_grp"] = None

        entry, _ = report_for("star_2025jul", star)

        self.assertIsNone(entry["validation"]["mismatches"]["CNT_GRP"]["examples"][0]["CNT_GRP_py"])
        # A text value is shown in quotes, so a stray space or a number stored as text can be seen.
        self.assertIn(f"    First difference: PROVIDER_ID {star.loc[row, 'PROVIDER_ID']}: "
                      "Python (missing), SAS '3) # of groups=5'", problem_lines(entry))

    def test_a_text_value_that_spells_inf_is_shown_as_text(self):
        # The JSON writes infinity as "inf". In a text column the same letters are just text.
        star = table("star_2025jul")
        row = star.index[star["cnt_grp"] == "3) # of groups=5"][0]
        star.loc[row, "cnt_grp"] = "inf"

        lines = problem_lines(report_for("star_2025jul", star)[0])

        self.assertIn("- CNT_GRP: 1 row differs.", lines)
        self.assertIn(f"    First difference: PROVIDER_ID {star.loc[row, 'PROVIDER_ID']}: "
                      "Python 'inf', SAS '3) # of groups=5'", lines)
        # SAS holds text here too, so nothing is said about the type of the column.
        self.assertNotIn(AS_TEXT_MEANS, lines)

    def test_a_number_column_stored_as_text_is_marked_as_compared_as_text(self):
        # Text never equals a number, so the row differs although the digits look the same.
        national = table("national_average_2025jul")
        first, second = national.columns[:2]
        national[[first, second]] = national[[first, second]].astype(str)

        lines = problem_lines(report_for("national_average_2025jul", national)[0])

        self.assertIn("- SUMMARY_SCORE_NAT: 1 row differs. Compared as text.", lines)
        self.assertIn("- SUMMARY_SCORE_NAT_PEER3: 1 row differs. Compared as text.", lines)
        # Explained once, after the columns, however many of them it applies to.
        self.assertEqual(lines.count(AS_TEXT_MEANS), 1)
        self.assertEqual(lines[-1], AS_TEXT_MEANS)

    def test_numbers_kept_as_python_objects_are_compared_as_text_too(self):
        # np.where(..., None) makes a column of type object. It holds numbers and no text,
        # so the report must not say that Python holds text.
        star = table("star_2025jul")
        star["star"] = np.where(star["report_indicator"] == 1, star["star"], None)
        self.assertEqual(star["star"].dtype, object)

        lines = problem_lines(report_for("star_2025jul", star)[0])

        self.assertIn("- STAR: 2,891 rows differ. Compared as text.", lines)
        self.assertEqual(lines[-1], AS_TEXT_MEANS)

    def test_a_column_that_holds_nothing_gets_no_sentence_about_its_type(self):
        # Every differing row is already explained: Python has no value there.
        star = table("star_2025jul")
        star["star"] = None

        lines = problem_lines(report_for("star_2025jul", star)[0])

        self.assertIn("- STAR: 2,891 rows differ (2,891 where Python has no value and SAS has one).", lines)
        self.assertNotIn(AS_TEXT_MEANS, lines)

    def test_compared_as_text_does_not_depend_on_the_order_of_the_rows(self):
        # In this order the first rows are hospitals with no star in SAS, so the example
        # rows alone do not show that the SAS column holds numbers.
        star = table("star_2025jul").sort_values("PROVIDER_ID", ascending=False, ignore_index=True)
        star["star"] = star["star"].map(lambda value: "NA" if pd.isna(value) else str(int(value)))

        entry, _ = report_for("star_2025jul", star)
        lines = problem_lines(entry)

        examples = entry["validation"]["mismatches"]["STAR"]["examples"]
        self.assertTrue(all(example["STAR_sas"] is None for example in examples), examples)
        self.assertIn("- STAR: 4,566 rows differ (1,675 where SAS has no value and Python has one). "
                      "Compared as text.", lines)
        self.assertEqual(lines[-1], AS_TEXT_MEANS)

    def test_a_text_column_after_the_fifth_is_still_marked_and_explained(self):
        name = "measure_average_stddev_2025jul"
        stats = table(name)
        stats.iloc[:, 3:8] += 1  # five number columns differ
        sixth = stats.columns[8]
        stats[sixth] = stats[sixth].astype(str)  # the sixth is text
        stats.iloc[:, 9] += 1  # the seventh is a number column again

        lines = problem_lines(report_for(name, stats)[0])

        self.assertIn(f"- {sixth.upper()}: 5 rows differ. Compared as text.", lines)
        # The number columns beside it are not marked.
        self.assertIn(f"- {stats.columns[3].upper()}: 5 rows differ. Largest difference 1.", lines)
        self.assertIn(f"- {stats.columns[9].upper()}: 5 rows differ. Largest difference 1.", lines)
        self.assertEqual(lines[-2:], [
            f"A first difference is shown for the first 5 columns only. The rest are in {JSON_NAME}.",
            AS_TEXT_MEANS,
        ])
        # The heading, 7 columns, 5 rows and the two sentences above. Nothing else.
        self.assertEqual(len(lines), 1 + 7 + 5 + 2)

    def test_a_column_where_every_differing_row_is_missing_on_one_side_is_not_marked(self):
        # Text where SAS has nothing, and nothing where SAS has a number. No row has a value
        # on both sides, so the type of the column is not what makes the rows differ.
        star = table("star_2025jul")
        star["star"] = np.where(star["star"].isna(), "none", None)

        lines = problem_lines(report_for("star_2025jul", star)[0])

        self.assertIn("- STAR: 4,566 rows differ (2,891 where Python has no value and SAS has one; "
                      "1,675 where SAS has no value and Python has one).", lines)
        self.assertNotIn(AS_TEXT_MEANS, lines)

    def test_small_noise_is_not_shown_as_the_largest_difference_of_a_failing_column(self):
        # Only missing values differ here, so the numeric gap is far inside the tolerance.
        name = "measure_average_stddev_2025jul"
        stats = table(name)
        column = stats.columns[3]
        stats.loc[stats["_STAT_"] == "MEAN", column] = np.nan

        lines = problem_lines(report_for(name, stats)[0])

        self.assertFalse(any("Largest difference" in line for line in lines), lines)

    def test_a_real_numeric_difference_shows_its_size(self):
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 0.5

        lines = problem_lines(report_for("national_average_2025jul", national)[0])

        self.assertIn("- SUMMARY_SCORE_NAT: 1 row differs. Largest difference 0.5.", lines)

    def test_a_gap_just_outside_the_tolerance_shows_its_size(self):
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 1e-10

        lines = problem_lines(report_for("national_average_2025jul", national)[0])

        self.assertIn("- SUMMARY_SCORE_NAT: 1 row differs. Largest difference 1e-10.", lines)

    def test_a_large_difference_is_written_out_in_full(self):
        # 2.56e+03 would read like another tiny number to someone who does not know the notation.
        name = "measure_average_stddev_2025jul"
        stats = table(name)
        column = stats.columns[3]
        stats.loc[stats["_STAT_"] == "N", column] += 2564

        lines = problem_lines(report_for(name, stats)[0])

        self.assertIn(f"- {column.upper()}: 1 row differs. Largest difference 2,564.", lines)

    def test_a_table_with_no_key_says_row_1(self):
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 1

        _, report = report_for("national_average_2025jul", national)
        text = format_report(report)

        self.assertIn("First difference: row 1: Python ", text)
        self.assertNotIn("_ROW_POSITION_", text)
        self.assertIn("row position", text)

    def test_each_kind_of_difference_is_said_in_plain_words(self):
        name = "measure_average_stddev_2025jul"
        columns = list(table(name).columns)
        first, second = columns[3], columns[4]

        def changed(change):
            stats = table(name)
            return problem_lines(report_for(name, change(stats))[0])

        cases = {
            "missing column": (
                lambda stats: stats.drop(columns=[first]),
                f"1 SAS column missing in Python: {first.upper()}",
            ),
            "extra column": (
                lambda stats: stats.assign(extra=1.0),
                "1 Python column not in SAS: EXTRA",
            ),
            "column order": (
                lambda stats: stats[columns[:3] + [second, first] + columns[5:]],
                f"Same columns, but in a different order. The first difference is column 4: "
                f"{second.upper()} in Python, {first.upper()} in SAS.",
            ),
            "key column absent": (
                lambda stats: stats.drop(columns=["_STAT_"]),
                "Rows are matched by _STAT_, and the Python table has no _STAT_ column, "
                "so no values were compared.",
            ),
            "row count": (
                lambda stats: stats.iloc[:4],
                "Row count differs: Python has 4, SAS has 5.",
            ),
            "row missing in Python": (
                lambda stats: stats.iloc[:4],
                "1 row in SAS but not in Python: _STAT_ STD",
            ),
            "row only in Python": (
                lambda stats: pd.concat([stats, stats.iloc[[0]].assign(_STAT_="MEDIAN")], ignore_index=True),
                "1 row in Python but not in SAS: _STAT_ MEDIAN",
            ),
            "repeated key": (
                lambda stats: pd.concat([stats, stats.iloc[[0]]], ignore_index=True),
                "1 _STAT_ value repeated in Python: N",
            ),
            "blank key": (
                lambda stats: stats.assign(_STAT_=stats["_STAT_"].where(stats["_STAT_"] != "STD", "")),
                "1 blank _STAT_ value in Python.",
            ),
        }
        for label, (change, sentence) in cases.items():
            with self.subTest(label):
                self.assertIn(sentence, changed(change))

    def test_a_table_without_its_key_column_says_nothing_more_about_values(self):
        name = "measure_average_stddev_2025jul"

        lines = problem_lines(report_for(name, table(name).drop(columns=["_STAT_"]))[0])

        self.assertEqual(lines, [
            "1 SAS column missing in Python: _STAT_",
            "Rows are matched by _STAT_, and the Python table has no _STAT_ column, so no values were compared.",
        ])

    def test_keys_that_cannot_be_matched_say_no_values_were_compared(self):
        name = "measure_average_stddev_2025jul"
        stats = table(name)
        cases = {
            "repeated key": pd.concat([stats, stats.iloc[[0]]], ignore_index=True),
            "blank key": stats.assign(_STAT_=stats["_STAT_"].where(stats["_STAT_"] != "STD", "")),
        }
        for label, changed in cases.items():
            with self.subTest(label):
                lines = problem_lines(report_for(name, changed)[0])

                self.assertEqual(lines[-1], "Rows could not be matched one to one, so no values were compared.")

    def test_column_order_is_not_mentioned_when_a_column_is_missing(self):
        # The order differs only because a column is gone; saying both would name one cause twice.
        name = "measure_average_stddev_2025jul"
        stats = table(name)

        lines = problem_lines(report_for(name, stats.drop(columns=[stats.columns[3]]))[0])

        self.assertFalse(any("different order" in line for line in lines), lines)

    def test_extra_rows_in_a_table_with_no_key_are_named_by_position(self):
        national = table("national_average_2025jul")
        doubled = pd.concat([national, national], ignore_index=True)

        lines = problem_lines(report_for("national_average_2025jul", doubled)[0])

        self.assertIn("Row count differs: Python has 2, SAS has 1.", lines)
        self.assertIn("1 row in Python but not in SAS: row 2", lines)

    def test_row_positions_are_listed_in_number_order(self):
        # The validator stores positions as text, where 10 sorts before 2.
        twelve = pd.concat([table("national_average_2025jul")] * 12, ignore_index=True)

        lines = problem_lines(report_for("national_average_2025jul", twelve)[0])

        self.assertIn("11 rows in Python but not in SAS: row 2, row 3, row 4, row 5, row 6 and 6 more", lines)

    def test_a_list_of_exactly_five_names_is_shown_whole(self):
        name = "measure_average_stddev_2025jul"

        lines = problem_lines(report_for(name, table(name).iloc[:0])[0])

        self.assertIn("5 rows in SAS but not in Python: _STAT_ MAX, MEAN, MIN, N, STD", lines)
        # No row is in both tables, so nothing is said about values that were never compared.
        self.assertFalse(any("every value is equal" in line for line in lines), lines)

    def test_long_lists_are_cut_short_in_the_text_but_not_in_the_json(self):
        star = table("star_2025jul").iloc[:10]

        entry, _ = report_for("star_2025jul", star)
        lines = problem_lines(entry)

        missing = entry["validation"]["missing_ids"]
        self.assertEqual(len(missing), 4556)
        self.assertIn(f"4,556 rows in SAS but not in Python: PROVIDER_ID {', '.join(missing[:5])} and 4,551 more",
                      lines)
        self.assertFalse(any("leading zeros" in line for line in lines), lines)

    def test_lost_leading_zeros_are_pointed_out(self):
        # What a spreadsheet does to the ids when a CSV is opened and saved again: 010040
        # becomes 10040 and matches no SAS row. Only ids that also end in 0 are changed
        # here, so that taking zeros off the end as well would not go unnoticed.
        process = table("process")
        ids = process["PROVIDER_ID"]
        changed = ids.str.startswith("0") & ids.str.endswith("0")
        process.loc[changed, "PROVIDER_ID"] = ids[changed].str.lstrip("0")
        lost = int(changed.sum())

        lines = problem_lines(report_for("process", process)[0])

        self.assertIn("Some PROVIDER_ID values in Python are the SAS values without their leading zeros. "
                      "Keep PROVIDER_ID as text.", lines)
        # Only the rows that are in both tables were compared, and that is the number given.
        self.assertGreater(lost, 0)
        self.assertEqual(lines[-1], f"In the {4566 - lost:,} rows and 31 columns that both tables have, "
                                    "every value is equal.")

    def test_hints_are_given_only_when_they_apply(self):
        # Rows are missing on both sides and a column is extra, but for other reasons.
        name = "measure_average_stddev_2025jul"
        stats = table(name)
        stats["_STAT_"] = stats["_STAT_"].str.lower()
        stats["run_id"] = 7

        lines = problem_lines(report_for(name, stats)[0])

        self.assertIn("5 rows in Python but not in SAS: _STAT_ max, mean, min, n, std", lines)
        self.assertFalse(any("leading zeros" in line or "UNNAMED" in line for line in lines), lines)

    def test_unnamed_hint_needs_a_column_called_unnamed(self):
        name = "national_average_2025jul"
        equal = "In the 1 row and 9 columns that both tables have, every value is equal."

        with_index = problem_lines(report_for(name, table(name).assign(**{"Unnamed: 0": 0, "run_id": 7}))[0])
        other_name = problem_lines(report_for(name, table(name).assign(unnamed_score=1.0))[0])

        self.assertEqual(with_index, ["2 Python columns not in SAS: UNNAMED: 0, RUN_ID", UNNAMED_HINT, equal])
        self.assertEqual(other_name, ["1 Python column not in SAS: UNNAMED_SCORE", equal])

    def test_when_only_the_shape_differs_the_text_says_the_values_are_equal(self):
        # Otherwise a reader cannot tell "compared and equal" from "not looked at".
        star = table("star_2025jul").iloc[:4000].assign(run_id=7)

        lines = problem_lines(report_for("star_2025jul", star)[0])

        self.assertEqual(lines[:2], ["Row count differs: Python has 4,000, SAS has 4,566.",
                                     "1 Python column not in SAS: RUN_ID"])
        self.assertEqual(lines[-1], "In the 4,000 rows and 27 columns that both tables have, every value is equal.")

    def test_one_row_and_one_shared_column_are_enough_to_say_the_values_are_equal(self):
        # The national averages have one row and no key column.
        name = "national_average_2025jul"
        national = table(name).iloc[:, :1].assign(run_id=7)

        lines = problem_lines(report_for(name, national)[0])

        self.assertEqual(lines[1:], ["1 Python column not in SAS: RUN_ID",
                                     "In the 1 row and 1 column that both tables have, every value is equal."])

    def test_a_key_and_one_shared_value_column_are_enough_to_say_the_values_are_equal(self):
        name = "measure_average_stddev_2025jul"
        stats = table(name)

        lines = problem_lines(report_for(name, stats[["_STAT_", stats.columns[3]]])[0])

        self.assertEqual(lines[-1], "In the 5 rows and 2 columns that both tables have, every value is equal.")

    def test_a_table_with_only_its_key_column_says_nothing_about_equal_values(self):
        # Every row is matched, but no value column is in both tables, so no value was compared.
        name = "measure_average_stddev_2025jul"

        lines = problem_lines(report_for(name, table(name)[["_STAT_"]])[0])

        self.assertEqual(lines, ["48 SAS columns missing in Python: _TYPE_, _FREQ_, MORT_30_AMI, MORT_30_CABG, "
                                 "MORT_30_COPD and 43 more"])

    def test_a_row_missing_from_a_table_with_no_key_is_named_by_position(self):
        name = "national_average_2025jul"

        lines = problem_lines(report_for(name, table(name).iloc[:0])[0])

        self.assertIn("1 row in SAS but not in Python: row 1", lines)

    def test_several_repeated_keys_are_counted_as_keys(self):
        name = "measure_average_stddev_2025jul"
        stats = table(name)
        repeated = pd.concat([stats, stats.iloc[[0, 0, 1]]], ignore_index=True)

        lines = problem_lines(report_for(name, repeated)[0])

        self.assertIn("2 _STAT_ values repeated in Python: MIN, N", lines)

    def test_five_differing_columns_all_show_a_row_and_six_do_not(self):
        # 5 is written out here, because the docs say 5.
        name = "measure_average_stddev_2025jul"
        closing = f"A first difference is shown for the first 5 columns only. The rest are in {JSON_NAME}."
        for count, last_is_closing in ((5, False), (6, True)):
            with self.subTest(count=count):
                stats = table(name)
                stats.iloc[:, 3:3 + count] += 1

                lines = problem_lines(report_for(name, stats)[0])

                self.assertEqual(sum(line.startswith("- ") for line in lines), count)
                self.assertEqual(sum("First difference:" in line for line in lines), 5)
                self.assertEqual(lines[-1] == closing, last_is_closing)

    def test_every_differing_column_is_named_and_the_first_few_show_a_row(self):
        name = "measure_average_stddev_2025jul"
        stats = table(name)
        stats.iloc[:, 3:] += 1
        differing = [column.upper() for column in stats.columns[3:]]

        entry, _ = report_for(name, stats)
        lines = problem_lines(entry)

        self.assertEqual(list(entry["validation"]["mismatches"]), differing)
        self.assertIn(f"Values differ in {len(differing)} columns:", lines)
        self.assertEqual([line for line in lines if line.startswith("- ")],
                         [f"- {column}: 5 rows differ. Largest difference 1." for column in differing])
        self.assertEqual(sum(line.lstrip().startswith("First difference:") for line in lines), SHOWN_COLUMNS)
        self.assertEqual(lines[-1], f"A first difference is shown for the first {SHOWN_COLUMNS} columns only. "
                                    f"The rest are in {JSON_NAME}.")

    def test_the_star_column_is_named_however_many_columns_differ(self):
        # SAS puts the results last, so a list cut short would leave out the star itself.
        star = table("star_2025jul")
        numbers = [column for column in star.columns if column not in ("PROVIDER_ID", "cnt_grp")]
        star[numbers] += 1

        lines = problem_lines(report_for("star_2025jul", star)[0])

        self.assertIn(f"Values differ in {len(numbers)} columns:", lines)
        self.assertIn("- STAR: 2,891 rows differ. Largest difference 1.", lines)

    def test_every_failing_output_gets_at_least_one_sentence(self):
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 1
        entry, _ = report_for("national_average_2025jul", national)
        # A failure the text has no sentence for, such as a check added to the validator later.
        entry["validation"]["mismatches"] = {}

        self.assertEqual(problem_lines(entry), [f"The comparison failed. See validation in {JSON_NAME}."])

    def test_outputs_with_nothing_wrong_get_no_sentences(self):
        outputs = build_report({"less100_measure": baseline()["less100_measure"]}, "test")["outputs"]

        self.assertEqual(problem_lines(outputs["LESS100_MEASURE"]), [])  # PASS
        self.assertEqual(problem_lines(outputs["STAR_2025JUL"]), [])  # NOT_CHECKED

    def test_start_with_the_first_failing_output_when_several_fail(self):
        stats = table("measure_average_stddev_2025jul")
        stats.iloc[:, 3] += 1
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 1

        several = format_report(build_report({"national_average_2025jul": national,
                                              "measure_average_stddev_2025jul": stats}, "test"))
        single = format_report(build_report({"national_average_2025jul": national}, "test"))

        self.assertIn("Start with MEASURE_AVERAGE_STDDEV_2025JUL.", several)
        self.assertLess(several.index("MEASURE_AVERAGE_STDDEV_2025JUL (Program 0): FAIL"),
                        several.index("NATIONAL_AVERAGE_2025JUL (Program 2): FAIL"))
        self.assertNotIn("Start with", single)


class CannotCompareTests(unittest.TestCase):
    def test_something_that_is_not_a_table_is_an_error_and_the_rest_still_report(self):
        for value in (None, [1, 2], "STAR_2025JUL.csv", table("national_average_2025jul").iloc[:, 0]):
            with self.subTest(type=type(value).__name__):
                report = build_report({"national_average_2025jul": value,
                                       "less100_measure": baseline()["less100_measure"]}, "test")
                entry = report["outputs"]["NATIONAL_AVERAGE_2025JUL"]

                self.assertEqual(entry["status"], "ERROR")
                self.assertEqual(entry["error"]["type"], "TypeError")
                self.assertIn(type(value).__name__, entry["error"]["message"])
                self.assertNotIn("validation", entry)
                self.assertEqual(report["outputs"]["LESS100_MEASURE"]["status"], "PASS")
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["counts"], {"PASS": 1, "FAIL": 0, "ERROR": 1, "NOT_CHECKED": 8})

    def test_columns_sas_would_treat_as_one_are_named(self):
        # SAS ignores case in names, pandas does not, so generated code can make both.
        national = table("national_average_2025jul").assign(summary_score_nat=1.0)
        # A space at the end is the same name to SAS too. The quotes make it visible.
        national["Pt_Exp_Grp_Score_Nat "] = 1.0

        entry, _ = report_for("national_average_2025jul", national)

        self.assertEqual(entry["status"], "ERROR")
        self.assertEqual(entry["error"]["type"], "ValueError")
        self.assertEqual(entry["error"]["message"],
                         "The Python table has columns that SAS would treat as one: "
                         "'Summary_Score_Nat' and 'summary_score_nat'; "
                         "'Pt_Exp_Grp_Score_Nat' and 'Pt_Exp_Grp_Score_Nat '")
        self.assertEqual(problem_lines(entry), [CANNOT_COMPARE, f"Reason: ValueError: {entry['error']['message']}"])

    def test_a_reason_of_several_lines_is_put_on_one(self):
        entry = {"program": 1, "key": "PROVIDER_ID", "status": "ERROR",
                 "error": {"type": "ValueError", "message": "first line\n  second line\n"}}

        self.assertEqual(problem_lines(entry), [CANNOT_COMPARE, "Reason: ValueError: first line second line"])

    def test_missing_sas_file_is_an_error_for_that_output(self):
        with tempfile.TemporaryDirectory() as empty, mock.patch.object(data_io, "SAS_OUTPUT_DIR", Path(empty)):
            entry, report = report_for("national_average_2025jul", table("national_average_2025jul"))

        self.assertEqual(entry["status"], "ERROR")
        self.assertEqual(report["status"], "FAIL")

    def test_compare_output_checks_one_table_with_the_right_key(self):
        result = compare_output("MEASURE_AVERAGE_STDDEV_2025JUL", table("measure_average_stddev_2025jul"))

        self.assertTrue(result["passed"])
        # Rows are matched by _STAT_, so their order does not matter.
        shuffled = table("measure_average_stddev_2025jul").iloc[::-1]
        self.assertTrue(compare_output("measure_average_stddev_2025jul", shuffled)["passed"])

    def test_compare_output_rejects_a_name_that_is_not_an_output(self):
        with self.assertRaises(ValueError) as caught:
            compare_output("WORK.PTEXP", table("ptexp"))

        self.assertIn("WORK.PTEXP", str(caught.exception))


class CsvFolderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_folder_with_all_ten_csvs_passes(self):
        for name, frame in baseline().items():
            frame.to_csv(self.folder / f"{name.upper()}.csv", index=False)
        # run_translator writes this file into output/data. It is not an output.
        (self.folder / "input_df.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        found = csv_tables(self.folder)
        report = build_report(found, "test")

        self.assertEqual(sorted(found), sorted(name.upper() for name in TEN_OUTPUTS))
        self.assertEqual(report["counts"], {"PASS": 10, "FAIL": 0, "ERROR": 0, "NOT_CHECKED": 0})

    def test_only_csv_files_count_whatever_the_case_of_their_names(self):
        table("national_average_2025jul").to_csv(self.folder / "National_Average_2025Jul.CSV", index=False)
        (self.folder / "STAR_2025JUL.txt").write_text("not a csv", encoding="utf-8")
        (self.folder / "PTEXP.csv").mkdir()  # a folder, not a file

        found = csv_tables(str(self.folder))  # text, as in the docs example

        self.assertEqual({name: path.name for name, path in found.items()},
                         {"NATIONAL_AVERAGE_2025JUL": "National_Average_2025Jul.CSV"})
        self.assertEqual(build_report(found, "test")["counts"], {"PASS": 1, "FAIL": 0, "ERROR": 0, "NOT_CHECKED": 9})

    def test_two_files_for_one_output_are_refused(self):
        # A disk that tells upper from lower case (Linux) can hold both names. This one may
        # not, so the folder listing is given by hand.
        both = [self.folder / "STAR_2025JUL.csv", self.folder / "star_2025jul.csv"]
        for path in both:
            path.write_text("PROVIDER_ID\n", encoding="utf-8")

        with mock.patch.object(Path, "iterdir", return_value=both), self.assertRaises(ValueError) as caught:
            csv_tables(self.folder)

        self.assertEqual(str(caught.exception),
                         "Two files hold STAR_2025JUL: STAR_2025JUL.csv and star_2025jul.csv. Keep one.")

    def test_ids_keep_their_leading_zeros_whatever_the_case_of_the_names(self):
        process = table("process").iloc[:3]
        self.assertTrue(all(value.isdigit() and value.startswith("0") for value in process["PROVIDER_ID"]))
        process.rename(columns={"PROVIDER_ID": "provider_id"}).to_csv(self.folder / "process.csv", index=False)

        validation = build_report(csv_tables(self.folder), "test")["outputs"]["PROCESS"]["validation"]

        # Read as numbers, 010001 would become 10001 and match no SAS row.
        self.assertEqual(validation["extra_ids"], [])
        self.assertEqual(len(validation["missing_ids"]), 4563)
        self.assertEqual(validation["mismatches"], {})

    def test_a_word_that_read_csv_takes_for_missing_is_still_text(self):
        # str() of a missing value is the word nan, and so was astype(str) before pandas 3.
        # SAS has a blank there, so the saved table must fail as it does in memory.
        for word in ("nan", "None", "NA", "NULL"):
            with self.subTest(word=word):
                star = table("star_2025jul")
                blank = star["cnt_grp"] == ""
                star["cnt_grp"] = star["cnt_grp"].astype(object).mask(blank, word)
                star.to_csv(self.folder / "STAR_2025JUL.csv", index=False)

                entry = build_report(csv_tables(self.folder), "test")["outputs"]["STAR_2025JUL"]
                in_memory, _ = report_for("star_2025jul", star)

                self.assertEqual(in_memory["validation"]["mismatches"]["CNT_GRP"]["count"], int(blank.sum()))
                self.assertEqual(entry["status"], "FAIL")
                self.assertEqual(entry["validation"]["mismatches"], in_memory["validation"]["mismatches"])

    def test_a_word_in_the_table_with_no_key_is_still_text(self):
        national = table("national_average_2025jul")
        column = national.columns[0]
        national[column] = national[column].astype(object)
        national.loc[0, column] = "NA"
        national.to_csv(self.folder / "NATIONAL_AVERAGE_2025JUL.csv", index=False)

        entry = build_report(csv_tables(self.folder), "test")["outputs"]["NATIONAL_AVERAGE_2025JUL"]
        in_memory, _ = report_for("national_average_2025jul", national)

        self.assertEqual(entry["status"], "FAIL")
        self.assertEqual(entry["validation"]["mismatches"], in_memory["validation"]["mismatches"])

    def test_file_with_no_header_is_treated_as_the_empty_table_it_came_from(self):
        # LESS100_MEASURE is empty in SAS, and pd.DataFrame().to_csv(index=False) writes a blank line.
        for content in ("\n", ""):
            with self.subTest(content=content):
                (self.folder / "LESS100_MEASURE.csv").write_text(content, encoding="utf-8")

                entry = build_report(csv_tables(self.folder), "test")["outputs"]["LESS100_MEASURE"]
                in_memory, _ = report_for("less100_measure", pd.DataFrame())

                self.assertEqual(entry["status"], "FAIL")
                self.assertEqual(entry["validation"]["missing_columns"], ["FREQ", "MEASURE_NAME"])
                self.assertEqual(entry["validation"], in_memory["validation"])

    def test_unreadable_file_is_an_error_for_that_output_only(self):
        (self.folder / "STAR_2025JUL.csv").write_bytes("a,b\n1,2\n".encode("utf-16"))
        baseline()["less100_measure"].to_csv(self.folder / "LESS100_MEASURE.csv", index=False)

        report = build_report(csv_tables(self.folder), "test")

        self.assertEqual(report["outputs"]["STAR_2025JUL"]["status"], "ERROR")
        self.assertEqual(report["outputs"]["LESS100_MEASURE"]["status"], "PASS")

    def test_a_file_pandas_cannot_parse_gives_a_reason_on_one_line(self):
        # The third row has more fields than the header. pandas ends its message with a line break.
        (self.folder / "PROCESS.csv").write_text("a,b\n1,2\n3,4,5,6\n", encoding="utf-8")

        entry = build_report(csv_tables(self.folder), "test")["outputs"]["PROCESS"]
        lines = problem_lines(entry)

        self.assertEqual(entry["status"], "ERROR")
        self.assertEqual(lines[0], CANNOT_COMPARE)
        self.assertRegex(lines[1], r"^Reason: ParserError: \S.*\S\Z")
        self.assertEqual(len(lines), 2)

    def test_csv_saved_with_its_row_index_fails_and_says_how_to_fix_it(self):
        baseline()["national_average_2025jul"].to_csv(self.folder / "NATIONAL_AVERAGE_2025JUL.csv")

        entry = build_report(csv_tables(self.folder), "test")["outputs"]["NATIONAL_AVERAGE_2025JUL"]

        self.assertEqual(entry["status"], "FAIL")
        self.assertEqual(entry["validation"]["extra_columns"], ["UNNAMED: 0"])
        self.assertEqual(problem_lines(entry)[:2], [
            "1 Python column not in SAS: UNNAMED: 0",
            "UNNAMED means a column with no name, usually the row numbers pandas adds. "
            "Save the table with to_csv(index=False).",
        ])

    def test_compare_output_reads_a_csv_path(self):
        path = self.folder / "NATIONAL_AVERAGE_2025JUL.csv"
        baseline()["national_average_2025jul"].to_csv(path, index=False)

        self.assertTrue(compare_output("national_average_2025jul", path)["passed"])


class ReadableReportTests(unittest.TestCase):
    def test_passing_report_gives_the_result_first_and_no_differences_section(self):
        text = format_report(baseline_report())
        lines = text.splitlines()

        self.assertEqual(lines[0], "SAS vs Python validation report")
        self.assertEqual(lines[1], "Checked:   the hand-written Python")
        # The same form as the save times of a folder, so the two can be compared.
        self.assertRegex(lines[2], r"^Created:   \d{4}-\d\d-\d\d \d\d:\d\d UTC$")
        self.assertIn("RESULT: PASS - 10 of 10 outputs match SAS", lines[:8])
        self.assertNotIn("WHAT DIFFERS", text)
        self.assertNotIn("Start with", text)
        self.assertIn("relative 1e-09, absolute 1e-12", text)
        for name in TEN_OUTPUTS:
            self.assertIn(name.upper(), text)
        text.encode("ascii")  # safe on any console

    def test_passing_report_says_what_the_empty_table_does_and_does_not_show(self):
        text = format_report(baseline_report())

        self.assertIn("LESS100_MEASURE has no rows in SAS, so only its columns were checked.", text)
        self.assertEqual(text.count("Note: "), 1)  # the other nine have rows

    def test_partial_report_marks_what_was_not_checked(self):
        text = format_report(build_report({name: baseline()[name] for name in PROGRAM0}, "test"))

        self.assertIn("RESULT: INCOMPLETE - 3 of 10 outputs match SAS, 7 not checked", text)
        star = next(line for line in text.splitlines() if "STAR_2025JUL" in line)
        self.assertIn("NOT CHECKED", star)

    def test_verdict_counts_every_kind_of_result(self):
        text = format_report(mixed_report())

        self.assertIn("\nRESULT: FAIL - 2 of 10 outputs match SAS, 2 differ, 1 could not be compared, "
                      "5 not checked\n", text)
        self.assertIn("\nNATIONAL_AVERAGE_2025JUL (Program 2): ERROR\n"
                      f"  {CANNOT_COMPARE}\n"
                      "  Reason: TypeError: Expected a pandas DataFrame or the Path of a CSV file, "
                      "got NoneType.\n", text)

    def test_summary_table_has_one_lined_up_row_per_output(self):
        report = mixed_report()
        lines = format_report(report).splitlines()
        top = next(number for number, line in enumerate(lines) if line.startswith("Program "))
        header, rule, rows = lines[top], lines[top + 1], lines[top + 2:top + 12]
        closeness = f"{report['outputs']['STD_DATA_2025JUL_ANALYSIS']['largest_difference']:.3g}"

        def cells(line):
            return re.split(r" {2,}", line)

        self.assertEqual(cells(header), ["Program", "Output", "Result", "Rows Python / SAS",
                                         "Rows matched by", "Largest difference"])
        self.assertEqual(set(rule), {"-", " "})
        self.assertEqual([cells(line) for line in rows], [
            ["0", "LESS100_MEASURE", "PASS", "0 / 0", "MEASURE_NAME", "-"],
            ["0", "MEASURE_AVERAGE_STDDEV_2025JUL", "FAIL", "4 / 5", "_STAT_", "see below"],
            ["0", "STD_DATA_2025JUL_ANALYSIS", "PASS", "4,566 / 4,566", "PROVIDER_ID", closeness],
            ["1", "OUTCOME_MORTALITY", "NOT CHECKED", "-", "PROVIDER_ID", "-"],
            ["1", "OUTCOME_SAFETY", "NOT CHECKED", "-", "PROVIDER_ID", "-"],
            ["1", "OUTCOME_READMISSION", "NOT CHECKED", "-", "PROVIDER_ID", "-"],
            ["1", "PTEXP", "NOT CHECKED", "-", "PROVIDER_ID", "-"],
            ["1", "PROCESS", "NOT CHECKED", "-", "PROVIDER_ID", "-"],
            ["2", "STAR_2025JUL", "FAIL", "4,566 / 4,566", "PROVIDER_ID", "see below"],
            ["2", "NATIONAL_AVERAGE_2025JUL", "ERROR", "-", "row position", "see below"],
        ])
        # Every cell starts under its heading.
        starts = [match.start() for match in re.finditer(r"\S+(?: \S+)*", header)]
        for line in [rule, *rows]:
            self.assertEqual([match.start() for match in re.finditer(r"\S+(?: \S+)*", line)], starts, line)

    def test_report_ends_by_explaining_each_result_word(self):
        lines = format_report(build_report({}, "test")).splitlines()

        legend = lines[lines.index("HOW TO READ THIS"):]
        for word in ("PASS", "FAIL", "ERROR", "NOT CHECKED"):
            with self.subTest(word):
                self.assertEqual(sum(line.startswith(f"  {word}  ") for line in legend), 1)

    def test_guide_states_the_rules_the_right_way_round(self):
        text = format_report(build_report({}, "test"))

        # 1e-12 is the fixed part and 1e-09 the part that grows with the number, as in the validator.
        self.assertIn("differ by no more than 1e-12 plus 1e-09 times\n  the SAS number (ignoring its sign).", text)
        for sentence in (
            "same columns in the same order, and every value equal within the tolerance.",
            "The details are listed above.",
            "The reason is listed above.",
            "or its program\n               was left out of this report.",
            "That is about 9 matching digits.",
            "so their order does not matter.",
            "Upper and lower case\n  in column names is ignored, as in SAS.",
            "Largest difference is the biggest gap between a Python number and the SAS number",
            "leaving out cells where either one is missing or infinite. 1e-13 means 0.0000000000001.",
            "First difference is the first row that differs, in the order of the Python table.",
            "Do not raise the tolerance.",
            f"Every column, with up to 5 example rows each, is in {JSON_NAME}.",
        ):
            with self.subTest(sentence):
                self.assertIn(sentence, text)

    def test_sections_come_in_reading_order_and_the_guide_is_last(self):
        # "see below" in the table and "listed above" in the guide depend on this order.
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 1
        text = format_report(build_report({"national_average_2025jul": national,
                                           "less100_measure": baseline()["less100_measure"]}, "test"))

        places = [text.index(mark) for mark in ("RESULT: ", "Program  Output", "Note: LESS100_MEASURE",
                                                "WHAT DIFFERS", "HOW TO READ THIS")]

        self.assertEqual(places, sorted(places))
        self.assertTrue(text.endswith(f"is in {JSON_NAME}.\n"))

    def test_start_with_counts_outputs_that_could_not_be_compared(self):
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 1

        text = format_report(build_report({"national_average_2025jul": national, "ptexp": None}, "test"))

        self.assertIn("Start with PTEXP. Outputs are listed in pipeline order, and each program reads\n"
                      "the outputs of the one before, so an early difference can cause the later ones.\n", text)

    def test_the_empty_table_note_is_only_for_a_pass(self):
        text = format_report(build_report({"less100_measure": pd.DataFrame()}, "test"))

        self.assertNotIn("Note:", text)

    def test_created_is_the_time_in_the_report_in_utc_on_a_24_hour_clock(self):
        # A report printed again from its saved JSON must show when it was made, not the time now.
        report = build_report({}, "test")

        report["created_at"] = "2026-01-02T15:04:05+00:00"
        self.assertEqual(format_report(report).splitlines()[2], "Created:   2026-01-02 15:04 UTC")
        report["created_at"] = "2026-01-02T23:04:05-05:00"
        self.assertEqual(format_report(report).splitlines()[2], "Created:   2026-01-03 04:04 UTC")

    def test_numbers_are_short_but_never_misleading(self):
        values = (2.5, 12.34, 999.4, 999.5, 1000, 1234567.8, 12_345_678.0, 999_999_999_999_999.0, 1e15, 1e300)

        shown = [validation_report._number(value) for value in values]

        # From 1,000 up e-notation would read like a tiny number. A runaway value keeps it,
        # because written out it would fill the screen.
        self.assertEqual(shown, ["2.5", "12.3", "999", "1,000", "1,000", "1,234,568", "12,345,678",
                                 "999,999,999,999,999", "1e+15", "1e+300"])

    def test_text_is_the_same_from_the_saved_json(self):
        national = table("national_average_2025jul")
        national.iloc[0, 0] = np.inf
        report = build_report({**baseline(), "national_average_2025jul": national}, "test")

        self.assertEqual(format_report(strict_loads(json.dumps(report, allow_nan=False))), format_report(report))

    def test_json_safe_turns_every_value_into_a_json_value(self):
        # The validator hands over plain Python values today. These are the ones pandas and
        # numpy could hand over after a change there.
        value = {1: (np.int64(3), np.bool_(True), np.float32(0.5)), "blank": pd.NA, "when": pd.NaT,
                 "bad": np.float64("nan"), "low": -np.inf, "odd": Decimal("1.5")}

        self.assertEqual(validation_report._json_safe(value),
                         {"1": [3, True, 0.5], "blank": None, "when": None, "bad": None, "low": "-inf",
                          "odd": "1.5"})

    def test_save_report_writes_both_files(self):
        with tempfile.TemporaryDirectory() as temp:
            json_path, text_path = save_report(baseline_report(), Path(temp) / "new" / "folder")

            self.assertEqual((json_path.name, text_path.name), (JSON_NAME, TEXT_NAME))
            saved = strict_loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(saved, baseline_report())
            self.assertEqual(text_path.read_text(encoding="utf-8"), format_report(saved))

    def test_saving_again_replaces_the_older_report(self):
        with tempfile.TemporaryDirectory() as temp:
            save_report(build_report({}, "an older run"), temp)
            json_path, text_path = save_report(baseline_report(), temp)

            self.assertEqual(strict_loads(json_path.read_text(encoding="utf-8")), baseline_report())
            self.assertEqual(text_path.read_text(encoding="utf-8"), format_report(baseline_report()))

    def test_save_report_without_a_folder_uses_the_default_one(self):
        # The default is swapped for a temporary folder so the test does not write into the project.
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(validation_report, "DEFAULT_REPORT_DIR", Path(temp) / "default"):
            json_path, text_path = save_report(build_report({}, "test"))

            self.assertEqual(json_path, Path(temp) / "default" / JSON_NAME)
            self.assertTrue(text_path.is_file())

    def test_save_report_refuses_values_strict_json_cannot_hold(self):
        report = build_report({}, "test")
        report["outputs"]["STAR_2025JUL"]["largest_difference"] = float("nan")

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                save_report(report, temp)

            # Nothing is left behind, so a text report never stands without its JSON.
            self.assertEqual(list(Path(temp).iterdir()), [])


class CommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.out = self.temp / "report"

    def test_baseline_passes_and_saves_both_files(self):
        code, printed = run_command("--baseline", "--out", self.out)

        self.assertEqual(code, 0)
        # The end of the output is what stays on screen, so it says what was checked as well.
        # A hand-written result must never be mistaken for an AI result.
        self.assertEqual(printed.splitlines()[-2:], [
            "Checked:   the hand-written Python in src/baseline (not AI output)",
            "RESULT: PASS - 10 of 10 outputs match SAS",
        ])
        saved = strict_loads((self.out / JSON_NAME).read_text(encoding="utf-8"))
        self.assertTrue(saved["passed"])
        self.assertEqual(saved["source"], "the hand-written Python in src/baseline (not AI output)")
        self.assertTrue((self.out / TEXT_NAME).is_file())

    def test_the_readme_command_saves_in_the_default_folder(self):
        # The folder that the README, the docs and .gitignore name.
        self.assertEqual(validation_report.DEFAULT_REPORT_DIR, ROOT / "output" / "validation_report")

        # No --out, as the README gives the command. The default is swapped for a temporary
        # folder so the test does not write into the project.
        with mock.patch.object(validation_report, "DEFAULT_REPORT_DIR", self.out):
            code, _ = run_command("--baseline")

        self.assertEqual(code, 0)
        self.assertTrue((self.out / JSON_NAME).is_file())
        self.assertTrue((self.out / TEXT_NAME).is_file())

    def test_folder_with_only_program0_is_incomplete_and_exits_1(self):
        folder = self.temp / "data"
        folder.mkdir()
        for name in PROGRAM0:
            baseline()[name].to_csv(folder / f"{name.upper()}.csv", index=False)

        code, printed = run_command(folder, "--out", self.out)

        self.assertEqual(code, 1)
        self.assertIn("RESULT: INCOMPLETE - 3 of 10 outputs match SAS, 7 not checked", printed)
        saved = strict_loads((self.out / JSON_NAME).read_text(encoding="utf-8"))
        self.assertIn(str(folder), saved["source"])
        self.assertIn("saved ", saved["source"])  # when the files were saved, to spot old ones

    def test_folder_with_none_of_the_outputs_is_incomplete_not_a_crash(self):
        # What output/data holds after a pipeline run that stopped early.
        folder = self.temp / "data"
        folder.mkdir()
        (folder / "input_df.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        code, printed = run_command(folder, "--out", self.out)

        self.assertEqual(code, 1)
        self.assertEqual(printed.splitlines()[-1], "RESULT: INCOMPLETE - 0 of 10 outputs match SAS, 10 not checked")
        self.assertEqual(printed.count("SAS vs Python validation report"), 1)
        self.assertEqual(printed.count("HOW TO READ THIS"), 1)
        saved = strict_loads((self.out / JSON_NAME).read_text(encoding="utf-8"))
        self.assertEqual(saved["source"], f"CSV files in {folder} (no file named like STAR_2025JUL.csv found)")

    def test_folder_with_a_wrong_table_exits_1(self):
        folder = self.temp / "data"
        folder.mkdir()
        national = table("national_average_2025jul")
        national.iloc[0, 0] += 1
        national.to_csv(folder / "NATIONAL_AVERAGE_2025JUL.csv", index=False)

        code, printed = run_command(folder, "--out", self.out)

        self.assertEqual(code, 1)
        self.assertIn("RESULT: FAIL", printed)
        # A failing report is saved too, and the whole report is printed, not only the last line.
        saved = strict_loads((self.out / JSON_NAME).read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "FAIL")
        report_text = (self.out / TEXT_NAME).read_text(encoding="utf-8")
        self.assertIn("WHAT DIFFERS", report_text)
        self.assertTrue(printed.startswith(report_text), printed[:200])
        self.assertIn(f"Saved {self.out / JSON_NAME}\nSaved {self.out / TEXT_NAME}\n", printed)

    def test_source_gives_the_oldest_and_newest_save_times_in_utc(self):
        folder = self.temp / "data"
        folder.mkdir()
        for name in ("less100_measure", "national_average_2025jul"):
            baseline()[name].to_csv(folder / f"{name.upper()}.csv", index=False)
        # The newer file comes first by name, so the times must really be put in order. It
        # was saved in the afternoon, so a 12-hour clock would show.
        os.utime(folder / "LESS100_MEASURE.csv", (1772643960, 1772643960))  # 2026-03-04 17:06 UTC
        os.utime(folder / "NATIONAL_AVERAGE_2025JUL.csv", (1767323040, 1767323040))  # 2026-01-02 03:04 UTC
        if hasattr(time, "tzset"):
            # A clock 9 hours ahead of UTC, so local time cannot pass for UTC on any machine.
            # Windows has no tzset; there the machine's own time zone is used.
            zone = mock.patch.dict(os.environ, {"TZ": "JST-9"})
            zone.start()
            self.addCleanup(time.tzset)
            self.addCleanup(zone.stop)
            time.tzset()

        run_command(folder, "--out", self.out)

        saved = strict_loads((self.out / JSON_NAME).read_text(encoding="utf-8"))
        self.assertEqual(saved["source"], f"CSV files in {folder} (saved 2026-01-02 03:04 to 2026-03-04 17:06 UTC)")

    def test_a_checkout_without_the_sas_csv_folder_still_reports(self):
        # The guard against checking the SAS CSV folder must not need that folder to exist.
        folder = self.temp / "data"
        folder.mkdir()

        with mock.patch.object(data_io, "SAS_OUTPUT_CSV_DIR", self.temp / "absent"):
            code, printed = run_command(folder, "--out", self.out)

        self.assertEqual(code, 1)
        self.assertIn("RESULT: INCOMPLETE", printed)

    def test_usage_mistakes_exit_2_say_why_and_write_nothing(self):
        a_file = self.temp / "a_file.csv"
        a_file.write_text("a,b\n1,2\n", encoding="utf-8")
        sas_folder = data_io.SAS_OUTPUT_CSV_DIR
        other_case = sas_folder.parent / sas_folder.name.lower()
        sas_results = "that folder holds the SAS results themselves"
        # Each case: the arguments, and words that the one-line message must hold.
        cases = {
            "no source": ([], "one of the arguments"),
            "both sources": (["--baseline", self.temp], "not allowed with argument"),
            "folder that does not exist": ([self.temp / "nowhere"], f"folder not found: {self.temp / 'nowhere'}"),
            "a file, not a folder": ([a_file], f"not a folder: {a_file}"),
            "an empty folder name": ([""], "a folder name is empty"),
            "the SAS results folder": ([sas_folder], sas_results),
            # A Mac opens the folder under either spelling. Linux says there is no such folder.
            "the SAS results folder in another letter case": (
                [other_case], sas_results if other_case.is_dir() else "folder not found: "),
            "the SAS results folder by another route": ([sas_folder / ".." / sas_folder.name], sas_results),
            "tolerance option": (["--baseline", "--rtol=1e-3"], "unrecognized arguments: --rtol=1e-3"),
        }
        for label, (argv, words) in cases.items():
            with self.subTest(label):
                code, _, errors = run_command_with_errors(*argv, "--out", self.out)

                self.assertEqual(code, 2)
                self.assertFalse(self.out.exists())
                self.assertIn(words, errors)
                self.assertNotIn("Traceback", errors)

    def test_an_empty_report_folder_name_is_refused(self):
        # What an unset shell variable gives: --out "$REPORT_DIR". It would mean the current folder.
        previous = os.getcwd()
        os.chdir(self.temp)
        self.addCleanup(os.chdir, previous)

        code, _ = run_command("--baseline", "--out", "")

        self.assertEqual(code, 2)
        self.assertFalse((self.temp / JSON_NAME).exists())

    def test_a_run_that_cannot_finish_exits_2_not_1(self):
        # Python ends with 1 after any error, and 1 promises that the report was written.
        folder = self.temp / "data"
        folder.mkdir()
        a_file = self.temp / "a_file"
        a_file.write_text("not a folder", encoding="utf-8")

        # Each case: the arguments, the situation, words that the error text must hold, and
        # whether a traceback comes with them. A folder that cannot be used gets one line.
        # A stop that nobody planned for shows where it happened.
        cases = {
            "two files for one output": (
                [folder, "--out", self.out],
                mock.patch.object(validation_report, "csv_tables", side_effect=ValueError("Two files hold PTEXP")),
                "ValueError: Two files hold PTEXP", True,
            ),
            "--out names a file": (
                [folder, "--out", a_file],
                contextlib.nullcontext(),
                "a_file", False,
            ),
            "an error nobody planned for": (
                ["--baseline", "--out", self.out],
                mock.patch.object(validation_report, "baseline_tables", side_effect=RuntimeError("broken")),
                "RuntimeError: broken", True,
            ),
        }
        for label, (argv, situation, words, has_traceback) in cases.items():
            with self.subTest(label), situation:
                code, printed, errors = run_command_with_errors(*argv)

                self.assertEqual(code, 2)
                self.assertFalse(self.out.exists())
                self.assertEqual(a_file.read_text(encoding="utf-8"), "not a folder")
                self.assertEqual(printed, "")  # no report text and no RESULT line
                self.assertIn(words, errors)
                self.assertEqual("Traceback (most recent call last)" in errors, has_traceback)


if __name__ == "__main__":
    unittest.main()
