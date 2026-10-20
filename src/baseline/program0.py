"""Program 0 - Data and Measure Standardization (2025 Jul).

Translation of "0 - Data and Measure Standardization_2025Jul.sas" and the
%keep_hos macro from Star_Macros.sas.

Steps:
  1. Count hospitals reporting each measure; exclude measures with <= 100.
  2. Drop hospitals with no included measures (%keep_hos).
  3. Output N/MIN/MAX/MEAN/STD of each measure (PROC MEANS).
  4. Standardize each measure to mean 0, std 1 (PROC STANDARD).
  5. Flip measures where lower is better, so higher always means better.
  6. Rename standardized measures to std_<measure> (analysis file).
  7. Build the measure list for each of the 5 measure groups (used by program 1).

Checked against the SAS outputs by tests/test_baseline_program0.py.
"""

from dataclasses import dataclass

import pandas as pd

MIN_HOSPITALS = 100  # measures reported by <= this many hospitals are excluded

# &measure_all. HAI_1-HAI_6 is a SAS numbered range list, expanded here.
MEASURE_ALL = [
    "MORT_30_AMI", "MORT_30_CABG", "MORT_30_COPD", "MORT_30_HF", "MORT_30_PN", "MORT_30_STK", "PSI_4_SURG_COMP",
    "COMP_HIP_KNEE", "HAI_1", "HAI_2", "HAI_3", "HAI_4", "HAI_5", "HAI_6", "PSI_90_SAFETY",
    "EDAC_30_AMI", "EDAC_30_HF", "EDAC_30_PN", "OP_32", "READM_30_CABG", "READM_30_COPD", "READM_30_HIP_KNEE",
    "READM_30_HOSP_WIDE", "OP_35_ADM", "OP_35_ED", "OP_36",
    "H_COMP_1_STAR_RATING", "H_COMP_2_STAR_RATING", "H_COMP_3_STAR_RATING", "H_COMP_5_STAR_RATING",
    "H_COMP_6_STAR_RATING", "H_COMP_7_STAR_RATING", "H_GLOB_STAR_RATING", "H_INDI_STAR_RATING",
    "HCP_COVID_19", "IMM_3", "OP_10", "OP_13", "OP_18B", "OP_22", "OP_23", "OP_29", "OP_8", "PC_01",
    "SAFE_USE_OF_OPIOIDS", "SEP_1",
]

# Measure group membership. Keys match the SAS work dataset names used by program 1.
MEASURE_GROUPS = {
    "outcomes_mortality": ["MORT_30_AMI", "MORT_30_CABG", "MORT_30_COPD", "MORT_30_HF", "MORT_30_PN",
                           "MORT_30_STK", "PSI_4_SURG_COMP"],
    "outcomes_safety": ["COMP_HIP_KNEE", "HAI_1", "HAI_2", "HAI_3", "HAI_4", "HAI_5", "HAI_6", "PSI_90_SAFETY"],
    "outcomes_readmission": ["EDAC_30_AMI", "EDAC_30_HF", "EDAC_30_PN", "OP_32", "READM_30_CABG", "READM_30_COPD",
                             "READM_30_HIP_KNEE", "READM_30_HOSP_WIDE", "OP_35_ADM", "OP_35_ED", "OP_36"],
    "ptexp": ["H_COMP_1_STAR_RATING", "H_COMP_2_STAR_RATING", "H_COMP_3_STAR_RATING", "H_COMP_5_STAR_RATING",
              "H_COMP_6_STAR_RATING", "H_COMP_7_STAR_RATING", "H_GLOB_STAR_RATING", "H_INDI_STAR_RATING"],
    "process": ["HCP_COVID_19", "IMM_3", "OP_10", "OP_13", "OP_18B", "OP_22", "OP_23", "OP_29", "OP_8", "PC_01",
                "SAFE_USE_OF_OPIOIDS", "SEP_1"],
}

# Measures where a lower raw score is better; negated after standardization.
FLIP_DIRECTION = [
    "MORT_30_AMI", "MORT_30_CABG", "MORT_30_COPD", "MORT_30_HF", "MORT_30_PN", "MORT_30_STK", "PSI_4_SURG_COMP",
    "COMP_HIP_KNEE", "HAI_1", "HAI_2", "HAI_3", "HAI_4", "HAI_5", "HAI_6", "PSI_90_SAFETY",
    "EDAC_30_AMI", "EDAC_30_HF", "EDAC_30_PN", "OP_32", "READM_30_CABG", "READM_30_COPD", "READM_30_HIP_KNEE",
    "READM_30_HOSP_WIDE", "OP_35_ADM", "OP_35_ED", "OP_36",
    "OP_22", "PC_01", "OP_18B", "OP_8", "OP_10", "OP_13", "SAFE_USE_OF_OPIOIDS",
]


@dataclass
class Program0Result:
    less100_measure: pd.DataFrame  # R.less100_measure
    measure_average_stddev: pd.DataFrame  # R.measure_average_stddev_2025Jul
    std_data_analysis: pd.DataFrame  # R.Std_data_2025Jul_analysis
    measure_in: list[str]  # included measures (&measure_in)
    group_measures: dict[str, list[str]]  # std_ column names per group (&measure_OM, &measure_OS, ...)


def _resolve(columns: pd.Index, names: list[str]) -> list[str]:
    """Map names to the dataset's own spelling; SAS variable names are case-insensitive."""
    by_upper = {c.upper(): c for c in columns}
    return [by_upper[n.upper()] for n in names]


def run(alldata: pd.DataFrame) -> Program0Result:
    measure_all = _resolve(alldata.columns, MEASURE_ALL)

    # 1. PROC TABULATE n*(measures): number of hospitals with a non-missing score.
    #    (SAS derives the name with tranwrd(_NAME_, '_N', ''), which would also strip
    #    any '_N' inside a measure name; none of the current measures contain one.)
    freq = alldata[measure_all].notna().sum()
    excluded = freq[freq <= MIN_HOSPITALS]
    measure_in = list(freq[freq > MIN_HOSPITALS].index)

    less100_measure = pd.DataFrame({"freq": excluded.to_numpy(float),
                                    "measure_name": excluded.index.to_numpy(object)})

    initial = alldata.drop(columns=list(excluded.index))

    # 2. %keep_hos: keep hospitals with at least one included measure.
    initial = initial.assign(Total_m_cnt=initial[measure_in].notna().sum(axis=1).astype(float))
    initial = initial[initial["Total_m_cnt"] >= 1].reset_index(drop=True)

    # 3. PROC MEANS default statistics. STD uses the n-1 denominator (VARDEF=DF).
    scores = initial[measure_in]
    stats = pd.DataFrame([scores.count(), scores.min(), scores.max(), scores.mean(), scores.std(ddof=1)],
                         index=["N", "MIN", "MAX", "MEAN", "STD"]).astype(float)
    measure_average_stddev = pd.concat([
        pd.DataFrame({"_TYPE_": 0.0, "_FREQ_": float(len(initial)), "_STAT_": stats.index}),
        stats.reset_index(drop=True),
    ], axis=1)

    # 4. PROC STANDARD mean=0 std=1. Missing values stay missing.
    std = (scores - scores.mean()) / scores.std(ddof=1)

    # 5. Re-direct measures so a higher score is always better.
    flip = [m for m in _resolve(initial.columns, FLIP_DIRECTION) if m in std.columns]
    std[flip] = -std[flip]

    # 6. Analysis file: drop the raw measures, append std_<measure> in &measure_in order.
    std_data_analysis = pd.concat([initial.drop(columns=measure_in),
                                   std.add_prefix("std_")], axis=1)

    # 7. Group lists, in &measure_all order. SAS matches names case-sensitively here.
    group_measures = {group: [f"std_{m}" for m in measure_in if m in members]
                      for group, members in MEASURE_GROUPS.items()}

    return Program0Result(less100_measure, measure_average_stddev, std_data_analysis, measure_in, group_measures)


def outputs(result: Program0Result) -> dict[str, pd.DataFrame]:
    """Program 0's permanent (R. library) datasets, keyed by SAS output name."""
    return {
        "less100_measure": result.less100_measure,
        "measure_average_stddev_2025jul": result.measure_average_stddev,
        "std_data_2025jul_analysis": result.std_data_analysis,
    }

