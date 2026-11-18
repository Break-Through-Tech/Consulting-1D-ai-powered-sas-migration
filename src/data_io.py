"""Paths and loaders for the SAS pipeline's input data and ground-truth outputs.

The .sas7bdat files are the ground truth. The output CSVs hold the same exact
values, made with src/export_exact_csvs.py. The rounded CSVs we were first given
are in data/Project_1/archive/original_rounded_csv/.
"""

from pathlib import Path

import pandas as pd
import pyreadstat

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = REPO_ROOT / "data" / "Project_1"

SAS_PROGRAMS_DIR = PROJECT_DIR / "SAS Programs"
INPUT_DIR = PROJECT_DIR / "Starrating"
SAS_OUTPUT_DIR = PROJECT_DIR / "SAS Output"
SAS_OUTPUT_CSV_DIR = PROJECT_DIR / "SAS Output CSV"

INPUT_DATASET = "alldata_2025jul"

# The 10 datasets that programs 0-2 write to the R. library, in pipeline order, and how
# the rows of each one are matched with SAS. src/validation_report.py looks it up here.
# (run_program1 in src/orchestrate.py still passes PROVIDER_ID for its five tables itself.)
#   program: the SAS program that writes the dataset.
#   key: the column that identifies a row. Rows are matched on it, so row order is not
#        compared. None means the table has no such column (the national averages are
#        a single row), so rows are compared by position.
# Everything else is the same for all 10: the report compares each one with its
# .sas7bdat file at the default tolerance.
OUTPUTS = {
    "less100_measure": {"program": 0, "key": "measure_name"},
    "measure_average_stddev_2025jul": {"program": 0, "key": "_STAT_"},
    "std_data_2025jul_analysis": {"program": 0, "key": "PROVIDER_ID"},
    "outcome_mortality": {"program": 1, "key": "PROVIDER_ID"},
    "outcome_safety": {"program": 1, "key": "PROVIDER_ID"},
    "outcome_readmission": {"program": 1, "key": "PROVIDER_ID"},
    "ptexp": {"program": 1, "key": "PROVIDER_ID"},
    "process": {"program": 1, "key": "PROVIDER_ID"},
    "star_2025jul": {"program": 2, "key": "PROVIDER_ID"},
    "national_average_2025jul": {"program": 2, "key": None},
}

OUTPUT_DATASETS = list(OUTPUTS)

SAS_PROGRAMS = [
    "Star_Macros.sas",
    "0 - Data and Measure Standardization_2025Jul.sas",
    "1 - First stage_Simple Average of Measure Scores_2025Jul.sas",
    "2 - Second Stage_Weighted Average and Categorize Star_2025Jul.sas",
]


def read_sas(path: Path) -> pd.DataFrame:
    """Read a .sas7bdat file. PROVIDER_ID stays a string (leading zeros matter)."""
    df, _ = pyreadstat.read_sas7bdat(str(path))
    return df


def load_input() -> pd.DataFrame:
    """The pipeline's input: one row per hospital, one column per measure score/denominator."""
    return read_sas(INPUT_DIR / f"{INPUT_DATASET}.sas7bdat")


def load_sas_output(name: str) -> pd.DataFrame:
    """A SAS-produced output dataset, e.g. load_sas_output("star_2025jul")."""
    return read_sas(SAS_OUTPUT_DIR / f"{name.lower()}.sas7bdat")


def load_sas_output_csv(name: str) -> pd.DataFrame:
    """The full-precision CSV copy of a SAS output dataset."""
    return pd.read_csv(SAS_OUTPUT_CSV_DIR / f"{name.upper()}.csv", dtype={"PROVIDER_ID": str})


def read_sas_program(filename: str) -> str:
    """Source text of one of the SAS programs, e.g. for passing to an LLM."""
    return (SAS_PROGRAMS_DIR / filename).read_text(encoding="utf-8", errors="replace")
