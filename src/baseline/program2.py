"""Program 2 - Second Stage: Weighted Average and Star Rating (2025 Jul).

Translation of "2 - Second Stage_Weighted Average and Categorize Star_2025Jul.sas"
and the %report, %kmeans, %nation_avg0, %nation_avg_peer and %nation_avg macros
from Star_Macros.sas.

Steps:
  1. Merge the 5 group scores from program 1 into one row per hospital.
  2. Weighted average of the group scores -> summary score. When a group is
     missing, its weight is redistributed across the groups that remain.
  3. %report: a hospital gets a star only with at least 3 groups of 3+ measures,
     one of which must be Mortality or Safety.
  4. Peer groups: reporting hospitals are split by their number of groups (3, 4, 5).
  5. %kmeans: within each peer group, cluster summary scores into 5 clusters
     (PROC FASTCLUS twice). The stars are the clusters ranked by their means.
  6. National averages of the summary score and of each group score.

Checked against the SAS outputs by tests/test_baseline_program2.py.
"""

import numpy as np
import pandas as pd

KEY = "PROVIDER_ID"

# R. library dataset from program 1 -> name of its grp_score in the merged file,
# in the order of the SAS merge statement (this sets the output column order).
SCORE_COLUMNS = {
    "outcome_mortality": "Std_Outcomes_Mortality_score",
    "outcome_readmission": "Std_Outcomes_Readmission_score",
    "outcome_safety": "Std_Outcomes_Safety_score",
    "ptexp": "Std_PatientExp_score",
    "process": "Std_Process_score",
}

# Fixed CMS weights, in the order of the SAS arrays:
# (group score, std_weight variable, weight variable, value).
WEIGHTS = [
    ("Std_PatientExp_score", "std_weight_PatientExperience", "weight_PatientExperience", 0.22),
    ("Std_Outcomes_Readmission_score", "std_weight_Readmission", "weight_Outcomes_Readmission", 0.22),
    ("Std_Outcomes_Mortality_score", "std_weight_Mortality", "weight_Outcomes_Mortality", 0.22),
    ("Std_Outcomes_Safety_score", "std_weight_safety", "weight_Outcomes_Safety", 0.22),
    ("Std_Process_score", "std_weight_Process", "weight_Process", 0.12),
]

# R. library dataset from program 1 -> %report's measure count variable.
COUNT_COLUMNS = {
    "outcome_mortality": "Outcomes_Mortality_cnt",
    "outcome_safety": "Outcomes_safety_cnt",
    "outcome_readmission": "Outcomes_Readmission_cnt",
    "ptexp": "Patient_Experience_cnt",
    "process": "Process_cnt",
}

MIN_MEASURES = 3  # a group counts toward reporting only with at least this many measures
MIN_GROUPS = 3  # a hospital needs at least this many counted groups to get a star

# Total_measure_group_cnt -> cnt_grp. SAS pads these to 23 characters, but the
# trailing blanks are not significant to SAS and do not survive reading the dataset.
PEER_GROUPS = {3: "1) # of groups=3", 4: "2) # of groups=4", 5: "3) # of groups=5"}

# PROC FASTCLUS options used by %kmeans. CONVERGE=0 is the stopping rule in
# fastclus(): iterate until the cluster seeds stop changing altogether.
MAX_CLUSTERS = 5
MAX_ITER = 1000
STRICT = 1.0

# R. library dataset from program 1 -> %nation_avg's output variable.
NATIONAL_GROUP_AVERAGES = {
    "outcome_mortality": "Out_Mrt_Grp_Score_Nat",
    "outcome_safety": "Out_Sft_Grp_Score_Nat",
    "outcome_readmission": "Out_Readm_grp_Score_Nat",
    "ptexp": "Pt_Exp_Grp_Score_Nat",
    "process": "Prc_of_Care_Grp_Score_Nat",
}


def summary_scores(group_outputs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """DATA all and DATA summary_score: group scores, weights and the summary score."""
    # MERGE ... BY provider_id, keeping only grp_score from each table.
    out = pd.concat([group_outputs[table].set_index(KEY)["grp_score"].rename(score)
                     for table, score in SCORE_COLUMNS.items()], axis=1).sort_index().reset_index()

    # I[k] = 1 if group k is missing. 1 - I1*W1 - ... - I5*W5 is then the share of
    # the total weight that belongs to the groups this hospital does have.
    remaining = 1 - sum(out[score].isna() * value for score, _, _, value in WEIGHTS)

    for _, std_weight, _, value in WEIGHTS:
        out[std_weight] = value

    # weight[k] = W[k] / remaining, and stays missing for a missing group. For
    # example, with Safety missing, Mortality's weight goes from 22/100 to 22/78.
    for score, _, weight, value in WEIGHTS:
        out[weight] = (value / remaining).where(out[score].notna())

    # sum_weight_ave[k] = weight[k] * score[k], or 0 for a missing group.
    out["summary_score"] = sum((out[weight] * out[score]).fillna(0) for score, _, weight, _ in WEIGHTS)
    return out


def report(group_outputs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """%report: measure counts per group and whether the hospital gets a star.

    SAS counts the non-missing measures in the program 0 analysis file. Program 1
    has already done that same count (total_cnt), so we read it from there and
    program 2 needs nothing but the program 1 tables.
    """
    counts = pd.concat([group_outputs[table].set_index(KEY)["total_cnt"].rename(count)
                        for table, count in COUNT_COLUMNS.items()], axis=1).sort_index()

    counted = counts >= MIN_MEASURES
    total_groups = counted.sum(axis=1)
    mort_safe = counted["Outcomes_Mortality_cnt"].astype(int) + counted["Outcomes_safety_cnt"].astype(int)

    return counts.assign(
        Total_measure_group_cnt=total_groups.astype(float),
        MortSafe_Group_cnt=mort_safe.astype(float),
        report_indicator=((mort_safe >= 1) & (total_groups >= MIN_GROUPS)).astype(float),
    ).reset_index()


def quintile_median_seeds(scores: np.ndarray) -> np.ndarray:
    """%kmeans step 1: the median of each quintile, used as the initial cluster seeds."""
    # PROC UNIVARIATE default percentile (PCTLDEF=5): with n*p/100 = j + g, take the
    # (j+1)th smallest value, or average the jth and (j+1)th when g = 0. Integer
    # arithmetic keeps g exact (2235 * 20 / 100 must be exactly 447).
    ordered = np.sort(scores)
    cutoffs = []
    for pct in (20, 40, 60, 80):
        j, g = divmod(len(ordered) * pct, 100)
        cutoffs.append((ordered[j - 1] + ordered[j]) / 2 if g == 0 else ordered[j])

    # grp = 1 if score <= P20, 2 if P20 < score <= P40, and so on.
    quintile = np.digitize(scores, cutoffs, right=True)

    # PROC MEANS median, by grp.
    return np.array([np.median(scores[quintile == k]) for k in range(MAX_CLUSTERS)])


def fastclus(scores: np.ndarray, seeds: np.ndarray, strict: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """PROC FASTCLUS on one variable: k-means from the given seeds.

    Returns the final cluster means and each hospital's cluster number (0-based).
    Each pass assigns every hospital to its nearest seed, then moves each seed to
    the mean of its cluster, until the seeds stop changing (CONVERGE=0).

    With STRICT=, a hospital farther than that from its nearest seed is left out of
    the means so an outlier cannot drag a cluster. It is still labelled with that
    nearest cluster: SAS gives it a negative cluster number, and %kmeans takes
    abs(cluster), so it gets a star like any other hospital.

    REPLACE=FULL (the FASTCLUS default, not set by %kmeans) needs no code here. Seed
    replacement only happens while selecting the initial seeds from the SEED= data
    set, and %kmeans supplies exactly MAXC=5 seeds, so there is no spare seed to swap
    in. This is inferred from the SAS documentation and output, not from SAS's source:
    all 6 FASTCLUS runs in "SAS Output.htm" print their initial seeds unchanged (the
    tests pin them). Two equal seeds were not tested; they do not occur in this data.

    A cluster left with no members keeps its old seed. That case does not occur in
    this data either, so the behavior is unverified against SAS.
    """
    centers = np.array(seeds, dtype=float)

    for _ in range(MAX_ITER):
        distances = np.abs(scores[:, np.newaxis] - centers[np.newaxis, :])
        nearest = distances.argmin(axis=1)

        new_centers = centers.copy()
        for k in range(len(centers)):
            members = nearest == k
            if strict is not None:
                members &= distances[:, k] <= strict
            if members.any():
                new_centers[k] = scores[members].mean()

        converged = np.array_equal(new_centers, centers)
        centers = new_centers
        if converged:
            break

    return centers, np.abs(scores[:, np.newaxis] - centers[np.newaxis, :]).argmin(axis=1)


def kmeans(scores: np.ndarray) -> np.ndarray:
    """%kmeans: star rating (1-5) for every hospital in one peer group."""
    # Step 1: run to full convergence from the quintile medians.
    first_pass_means, _ = fastclus(scores, quintile_median_seeds(scores))

    # Step 2: run again from those means, with strict=1 to limit the effect of outliers.
    cluster_means, clusters = fastclus(scores, first_pass_means, strict=STRICT)

    # Rank the clusters by mean: the lowest mean is star 1, the highest is star 5.
    stars_by_cluster = cluster_means.argsort().argsort() + 1
    return stars_by_cluster[clusters].astype(float)


def national_average(star: pd.DataFrame, group_outputs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """%nation_avg0, %nation_avg_peer and %nation_avg, merged into a single row."""
    reporting = star[star["report_indicator"] == 1]

    # %nation_avg0 and %nation_avg_peer: mean summary score of reporting hospitals,
    # overall and per number of groups (PROC TRANSPOSE makes each peer group a column).
    row = {"Summary_Score_Nat": reporting["summary_score"].mean()}
    for groups, mean in reporting.groupby("Total_measure_group_cnt")["summary_score"].mean().items():
        row[f"Summary_Score_Nat_peer{int(groups)}"] = mean

    # %nation_avg: mean group score of hospitals with at least 3 measures in the group.
    for table, name in NATIONAL_GROUP_AVERAGES.items():
        group = group_outputs[table]
        row[name] = group.loc[group["total_cnt"] >= MIN_MEASURES, "grp_score"].mean()

    return pd.DataFrame([row])


def run(group_outputs: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Program 2's permanent (R. library) datasets, keyed by SAS output name.

    Takes program 1's outputs, keyed the same way (program1.run or the SAS tables).
    """
    # MERGE summary_score1 report_indicator; BY provider_id.
    star = summary_scores(group_outputs).merge(report(group_outputs), on=KEY, how="outer", sort=True)

    # Hospitals that do not report keep a blank peer group and a missing star.
    star["cnt_grp"] = ""
    star["star"] = np.nan
    reporting = star["report_indicator"] == 1
    star.loc[reporting, "cnt_grp"] = star.loc[reporting, "Total_measure_group_cnt"].map(PEER_GROUPS)

    # Clustering is done separately within each peer group, so the same star can
    # mean a different summary score range in different peer groups.
    for peer_group in PEER_GROUPS.values():
        members = star["cnt_grp"] == peer_group
        star.loc[members, "star"] = kmeans(star.loc[members, "summary_score"].to_numpy(dtype=float))

    return {
        "star_2025jul": star,
        "national_average_2025jul": national_average(star, group_outputs),
    }
