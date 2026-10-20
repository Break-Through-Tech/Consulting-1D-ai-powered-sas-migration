"""The validator must catch real discrepancies, not just pass everything."""

import numpy as np
import pandas as pd
import pytest

from sas_migration.validate import compare_datasets


@pytest.fixture
def sas():
    return pd.DataFrame({"PROVIDER_ID": ["010001", "010005", "010006"],
                         "score": [0.5, np.nan, -1.25],
                         "grp": ["a", "b ", "c"]})


def test_identical_passes(sas):
    assert compare_datasets(sas.copy(), sas, "t").passed


def test_row_order_ignored_when_keyed(sas):
    assert compare_datasets(sas.iloc[::-1], sas, "t").passed


def test_tiny_float_noise_passes(sas):
    py = sas.assign(score=sas["score"] + 1e-12)
    assert compare_datasets(py, sas, "t").passed


def test_value_difference_fails(sas):
    py = sas.assign(score=[0.5, np.nan, -1.2])
    report = compare_datasets(py, sas, "t")
    assert not report.passed
    diff = next(d for d in report.column_diffs if d.column == "score")
    assert diff.n_value_mismatch == 1 and diff.max_abs_diff == pytest.approx(0.05)


def test_missing_pattern_difference_fails(sas):
    py = sas.assign(score=[0.5, 0.0, -1.25])
    diff = next(d for d in compare_datasets(py, sas, "t").column_diffs if d.column == "score")
    assert diff.n_missing_mismatch == 1


def test_string_difference_fails_but_sas_padding_ignored(sas):
    assert compare_datasets(sas.assign(grp=["a", "b", "c"]), sas, "t").passed
    assert not compare_datasets(sas.assign(grp=["a", "x", "c"]), sas, "t").passed


def test_column_problems_fail(sas):
    assert compare_datasets(sas.drop(columns="grp"), sas, "t").missing_columns == ["grp"]
    assert compare_datasets(sas.assign(extra=1), sas, "t").extra_columns == ["extra"]
    assert not compare_datasets(sas[["PROVIDER_ID", "grp", "score"]], sas, "t").column_order_matches


def test_row_count_and_key_mismatch_fail(sas):
    report = compare_datasets(sas.iloc[:2], sas, "t")
    assert not report.passed and report.key_mismatch == 1
