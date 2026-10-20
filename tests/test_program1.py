"""Program 1 baseline translation reproduces the SAS outputs."""

import numpy as np
import pandas as pd
import pytest

from sas_migration.baseline import program1


@pytest.fixture
def analysis():
    return pd.DataFrame({"PROVIDER_ID": ["010001", "010005", "010006"],
                         "std_A": [1.0, np.nan, 3.0],
                         "std_B": [np.nan, np.nan, 5.0],
                         "other": [9.0, 9.0, 9.0]})


def test_grp_score_columns(analysis):
    out = program1.grp_score(analysis, ["std_A", "std_B"])
    assert list(out.columns) == ["PROVIDER_ID", "std_A", "std_B", "C1", "C2", "total_cnt", "measure_wt",
                                 "score_before_std", "Mean", "StdDev", "grp_score"]


def test_grp_score_values(analysis):
    out = program1.grp_score(analysis, ["std_A", "std_B"])
    np.testing.assert_array_equal(out["C1"], [1, 0, 1])
    np.testing.assert_array_equal(out["C2"], [0, 0, 1])
    np.testing.assert_array_equal(out["total_cnt"], [1, 0, 2])
    np.testing.assert_array_equal(out["measure_wt"], [1, np.nan, 0.5])
    np.testing.assert_array_equal(out["score_before_std"], [1, np.nan, 4])
    assert out["Mean"].eq(2.5).all() and out["StdDev"].eq(np.sqrt(4.5)).all()
    np.testing.assert_allclose(out["grp_score"], [-np.sqrt(0.5), np.nan, np.sqrt(0.5)])
