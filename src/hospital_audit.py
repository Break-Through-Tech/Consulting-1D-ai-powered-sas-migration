"""Inspect hospital ratings produced by a saved end-to-end pipeline run.

Reads generated CSV files only. Does not execute generated code or
modify the original hospital data.
"""

import argparse
from pathlib import Path

import pandas as pd


GROUP_TABLES = {
    "Mortality": "OUTCOME_MORTALITY",
    "Safety": "OUTCOME_SAFETY",
    "Readmission": "OUTCOME_READMISSION",
    "Patient experience": "PTEXP",
    "Process": "PROCESS",
}

FINAL_GROUP_COLUMNS = {
    "Mortality": "Std_Outcomes_Mortality_score",
    "Safety": "Std_Outcomes_Safety_score",
    "Readmission": "Std_Outcomes_Readmission_score",
    "Patient experience": "Std_PatientExp_score",
    "Process": "Std_Process_score",
}


def read_table(run_dir, name):
    path = Path(run_dir) / "data" / f"{name}.csv"

    if not path.exists():
        raise FileNotFoundError(f"Missing pipeline output: {path}")

    return pd.read_csv(
        path,
        dtype={"PROVIDER_ID": str},
        low_memory=False,
    )


def clean_value(value):
    if pd.isna(value):
        return None

    if hasattr(value, "item"):
        return value.item()

    return value


def find_hospital(table, provider_id, table_name):
    if "PROVIDER_ID" not in table.columns:
        raise ValueError(
            f"{table_name} is missing PROVIDER_ID"
        )

    matched = table.loc[
        table["PROVIDER_ID"] == provider_id
    ]

    if matched.empty:
        raise ValueError(
            f"Hospital {provider_id} not found in {table_name}"
        )

    if len(matched) > 1:
        raise ValueError(
            f"Duplicate provider ID {provider_id} in {table_name}"
        )

    return matched.iloc[0]


def observed_rating_ranges(star_table, peer_group):
    """Observed ranges, not theoretical clustering cutoffs."""

    if peer_group is None:
        return []

    matching_group = (
        star_table["cnt_grp"].astype("string").str.strip()
        == str(peer_group).strip()
    ).fillna(False)

    peers = star_table.loc[matching_group].dropna(
        subset=["star", "summary_score"]
    )

    ranges = []

    for rating, group in peers.groupby("star", sort=True):
        ranges.append({
            "star": int(rating),
            "count": len(group),
            "minimum_score": float(
                group["summary_score"].min()
            ),
            "maximum_score": float(
                group["summary_score"].max()
            ),
        })

    return ranges


def audit_hospital(run_dir, provider_id):
    """Return a hospital's saved calculation stages and rating."""

    provider_id = str(provider_id).strip()

    if not provider_id:
        raise ValueError("Provider ID cannot be empty")

    star_table = read_table(run_dir, "STAR_2025JUL")
    star_row = find_hospital(
        star_table, provider_id, "STAR_2025JUL"
    )

    standardized = read_table(
        run_dir, "STD_DATA_2025JUL_ANALYSIS"
    )
    standardized_row = find_hospital(
        standardized,
        provider_id,
        "STD_DATA_2025JUL_ANALYSIS",
    )

    standardized_columns = {
        name.casefold(): name
        for name in standardized.columns
    }

    groups = {}

    for label, table_name in GROUP_TABLES.items():
        group_table = read_table(run_dir, table_name)
        group_row = find_hospital(
            group_table, provider_id, table_name
        )

        measures = {}

        for column in group_table.columns:
            if not column.lower().startswith("std_"):
                continue

            source_column = standardized_columns.get(
                column.casefold()
            )

            measures[column] = {
                "program0": (
                    clean_value(
                        standardized_row[source_column]
                    )
                    if source_column else None
                ),
                "program1": clean_value(
                    group_row[column]
                ),
            }

        groups[label] = {
            "output": table_name,
            "measure_count": clean_value(
                group_row["total_cnt"]
            ),
            "group_score": clean_value(
                group_row["grp_score"]
            ),
            "final_standardized_score": clean_value(
                star_row[FINAL_GROUP_COLUMNS[label]]
            ),
            "measures": measures,
        }

    peer_group = clean_value(star_row["cnt_grp"])

    if isinstance(peer_group, str):
        peer_group = peer_group.strip() or None

    rating = clean_value(star_row["star"])

    return {
        "provider_id": provider_id,
        "star_rating": (
            int(rating) if rating is not None else None
        ),
        "summary_score": clean_value(
            star_row["summary_score"]
        ),
        "peer_group": peer_group,
        "groups": groups,
        "observed_rating_ranges": observed_rating_ranges(
            star_table, peer_group
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Inspect a hospital's saved rating calculations"
    )
    parser.add_argument("run_dir")
    parser.add_argument("provider_id")

    args = parser.parse_args()

    report = audit_hospital(
        args.run_dir, args.provider_id
    )

    print("\n=== HOSPITAL RATING AUDIT ===")
    print("Provider ID:", report["provider_id"])
    print("Stars:", report["star_rating"])
    print("Summary score:", report["summary_score"])
    print("Grouping category:", report["peer_group"])

    print("\n=== FIVE QUALITY GROUPS ===")

    for label, group in report["groups"].items():
        print(
            f"{label}: "
            f"group score={group['group_score']}, "
            f"final standardized score="
            f"{group['final_standardized_score']}, "
            f"measures={group['measure_count']}"
        )

    print("\n=== OBSERVED RATING RANGES ===")

    for entry in report["observed_rating_ranges"]:
        print(
            f"{entry['star']} stars: "
            f"{entry['minimum_score']:.6f} to "
            f"{entry['maximum_score']:.6f} "
            f"({entry['count']} hospitals)"
        )

    print("\nAudit complete. No files changed.")


if __name__ == "__main__":
    main()
