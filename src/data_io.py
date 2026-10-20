"""Paths and loaders for the SAS pipeline's input data and ground-truth outputs.

Use the .sas7bdat files as ground truth: the CSV exports are rounded
(differences up to ~5e-7), which is too coarse for exact validation.
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

# Datasets written to the R. library by programs 0-2, in pipeline order.
OUTPUT_DATASETS = [
    "less100_measure",
    "measure_average_stddev_2025jul",
    "std_data_2025jul_analysis",
    "outcome_mortality",
    "outcome_safety",
    "outcome_readmission",
    "ptexp",
    "process",
    "star_2025jul",
    "national_average_2025jul",
]

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
    """The (rounded) CSV export of a SAS output dataset."""
    return pd.read_csv(SAS_OUTPUT_CSV_DIR / f"{name.upper()}.csv", dtype={"PROVIDER_ID": str})


def read_sas_program(filename: str) -> str:
    """Source text of one of the SAS programs, e.g. for passing to an LLM."""
    return (SAS_PROGRAMS_DIR / filename).read_text(encoding="utf-8", errors="replace")
