"""Program 0 baseline translation reproduces the SAS outputs."""

import pytest

from sas_migration import data_io
from sas_migration.baseline import program0
from sas_migration.validate import compare_datasets


@pytest.fixture(scope="module")
def result():
    return program0.run(data_io.load_input())


@pytest.mark.parametrize("name", ["less100_measure", "measure_average_stddev_2025jul", "std_data_2025jul_analysis"])
def test_output_matches_sas(result, name):
    report = compare_datasets(program0.outputs(result)[name], data_io.load_sas_output(name), name)
    assert report.passed, report.summary()


def test_group_measures_match_program1_outputs(result):
    # Program 1 writes each group's std_ measures first (after PROVIDER_ID), in group order.
    sas_names = {"outcomes_mortality": "outcome_mortality", "outcomes_safety": "outcome_safety",
                 "outcomes_readmission": "outcome_readmission", "ptexp": "ptexp", "process": "process"}
    for group, measures in result.group_measures.items():
        sas_cols = list(data_io.load_sas_output(sas_names[group]).columns)
        assert sas_cols[1:1 + len(measures)] == measures


def test_every_included_measure_in_exactly_one_group(result):
    grouped = [m for ms in result.group_measures.values() for m in ms]
    assert sorted(grouped) == sorted(f"std_{m}" for m in result.measure_in)
