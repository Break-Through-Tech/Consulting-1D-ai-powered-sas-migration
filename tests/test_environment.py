"""Smoke tests: the environment works and all project data loads as expected."""

import importlib

import numpy as np
import pytest

from sas_migration import data_io

# Shapes reported in SAS_Log.log.
EXPECTED_SHAPES = {
    "less100_measure": (0, 2),
    "measure_average_stddev_2025jul": (5, 49),
    "std_data_2025jul_analysis": (4566, 94),
    "outcome_mortality": (4566, 21),
    "outcome_safety": (4566, 23),
    "outcome_readmission": (4566, 29),
    "ptexp": (4566, 23),
    "process": (4566, 31),
    "star_2025jul": (4566, 27),
    "national_average_2025jul": (1, 9),
}


@pytest.mark.parametrize(
    "module",
    ["numpy", "pandas", "pyreadstat", "scipy.stats", "sklearn.cluster", "statsmodels.api",
     "datacompy", "anthropic", "pydantic", "dotenv"],
)
def test_import(module):
    importlib.import_module(module)


def test_input_loads():
    df = data_io.load_input()
    assert df.shape == (4566, 93)
    assert df["PROVIDER_ID"].is_unique
    assert df["PROVIDER_ID"].str.len().eq(6).all()  # leading zeros preserved


@pytest.mark.parametrize("name", data_io.OUTPUT_DATASETS)
def test_sas_output_matches_log_and_csv(name):
    sas = data_io.load_sas_output(name)
    csv = data_io.load_sas_output_csv(name)
    assert sas.shape == EXPECTED_SHAPES[name]
    assert list(sas.columns) == list(csv.columns)

    numeric = sas.select_dtypes("number").columns
    np.testing.assert_allclose(sas[numeric].to_numpy(float), csv[numeric].to_numpy(float),
                               rtol=0, atol=1e-6, equal_nan=True)


@pytest.mark.parametrize("filename", data_io.SAS_PROGRAMS)
def test_sas_program_readable(filename):
    assert len(data_io.read_sas_program(filename)) > 0
