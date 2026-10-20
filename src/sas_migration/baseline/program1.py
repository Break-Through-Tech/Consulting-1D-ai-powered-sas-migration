"""Program 1 - First Stage: Simple Average of Measure Scores (2025 Jul).

Translation of "1 - First stage_Simple Average of Measure Scores_2025Jul.sas"
and the %grp_score macro from Star_Macros.sas.

For each of the 5 measure groups:
  1. Count the hospital's non-missing standardized measures (C1-Cn, total_cnt).
  2. Average them: sum of non-missing scores times measure_wt = 1/total_cnt.
  3. Standardize the averages to mean 0, std 1 (PROC STANDARD) -> grp_score.
  4. Attach the pre-standardization mean and std (PROC MEANS) to every row.

Run as a script to validate against the SAS outputs:
    python -m sas_migration.baseline.program1
"""

import pandas as pd

KEY = "PROVIDER_ID"

# Program 0 group key -> R. library dataset that program 1 writes for it.
GROUP_OUTPUTS = {
    "outcomes_mortality": "outcome_mortality",
    "outcomes_safety": "outcome_safety",
    "outcomes_readmission": "outcome_readmission",
    "ptexp": "ptexp",
    "process": "process",
}


def grp_score(analysis: pd.DataFrame, measures: list[str]) -> pd.DataFrame:
    """%grp_score: one group's scores for every hospital in the analysis file."""
    scores = analysis[measures]

    # C[k] = 1 if measure k is reported, else 0.
    present = scores.notna().astype(float)
    present.columns = [f"C{k}" for k in range(1, len(measures) + 1)]
    total_cnt = present.sum(axis=1)

    # Only computed when total_cnt > 0; otherwise both stay missing. SAS multiplies
    # the sum by 1/total_cnt rather than dividing, so we do the same.
    measure_wt = (1 / total_cnt).where(total_cnt > 0)
    avg = scores.sum(axis=1, min_count=1) * measure_wt

    # PROC MEANS / PROC STANDARD over hospitals with a group score (std uses n-1).
    mean, std = avg.mean(), avg.std(ddof=1)

    return pd.concat([analysis[[KEY]], scores, present], axis=1).assign(
        total_cnt=total_cnt,
        measure_wt=measure_wt,
        score_before_std=avg,
        Mean=mean,
        StdDev=std,
        grp_score=(avg - mean) / std,
    )


def run(std_data_analysis: pd.DataFrame, group_measures: dict[str, list[str]]) -> dict[str, pd.DataFrame]:
    """Program 1's permanent (R. library) datasets, keyed by SAS output name.

    Takes program 0's analysis file and group measure lists (Program0Result).
    """
    return {GROUP_OUTPUTS[group]: grp_score(std_data_analysis, measures)
            for group, measures in group_measures.items()}


if __name__ == "__main__":
    from sas_migration import data_io
    from sas_migration.baseline import program0
    from sas_migration.validate import compare_datasets

    p0 = program0.run(data_io.load_input())
    for name, df in run(p0.std_data_analysis, p0.group_measures).items():
        print(compare_datasets(df, data_io.load_sas_output(name), name).summary())
