"""Compare saved hospital calculations with SAS and suggest investigation steps."""

import argparse
import math
import numbers
from pathlib import Path

import pandas as pd

from src.data_io import load_sas_output
from src.dependency_trace import trace_validation_report
from src.hospital_audit import find_hospital, read_table
from src.orchestrate import build_program1_plan, build_step_plan
from src.validate import DEFAULT_RTOL, DEFAULT_ATOL


# Hospital-level checkpoints in pipeline order.
CHECKPOINTS = [
    (0, "STD_DATA_2025JUL_ANALYSIS"),
    (1, "OUTCOME_MORTALITY"),
    (1, "OUTCOME_SAFETY"),
    (1, "OUTCOME_READMISSION"),
    (1, "PTEXP"),
    (1, "PROCESS"),
    (2, "STAR_2025JUL"),
]


def column_lookup(table):
    lookup = {}

    for column in table.columns:
        key = str(column).strip().casefold()

        if key in lookup:
            raise ValueError(
                f"Duplicate column after normalization: {column}"
            )

        lookup[key] = column

    return lookup


def missing(value):
    if isinstance(value, str):
        return not value.strip()
    return bool(pd.isna(value))


def values_match(sas_value, python_value):
    if missing(sas_value) or missing(python_value):
        return missing(sas_value) and missing(python_value)

    if (
        isinstance(sas_value, numbers.Number)
        or isinstance(python_value, numbers.Number)
    ):
        try:
            return math.isclose(
                float(sas_value),
                float(python_value),
                rel_tol=DEFAULT_RTOL,
                abs_tol=DEFAULT_ATOL,
            )
        except (TypeError, ValueError):
            pass

    return str(sas_value).strip() == str(python_value).strip()


def display_value(value):
    if missing(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def compare_checkpoint(run_dir, provider_id, program, name):
    python_table = read_table(run_dir, name)
    sas_table = load_sas_output(name)

    python_columns = column_lookup(python_table)
    sas_columns = column_lookup(sas_table)

    if "provider_id" not in sas_columns:
        raise ValueError(f"SAS {name} has no provider ID")

    if "provider_id" not in python_columns:
        raise ValueError(f"Python {name} has no provider ID")

    sas_table = sas_table.rename(
        columns={sas_columns["provider_id"]: "PROVIDER_ID"}
    )
    python_table = python_table.rename(
        columns={python_columns["provider_id"]: "PROVIDER_ID"}
    )

    sas_row = find_hospital(sas_table, provider_id, f"SAS {name}")
    python_row = find_hospital(
        python_table, provider_id, f"Python {name}"
    )

    sas_columns = column_lookup(sas_table)
    python_columns = column_lookup(python_table)

    differences = []

    all_columns = sorted(
        (set(sas_columns) | set(python_columns)) - {"provider_id"}
    )

    for key in all_columns:
        sas_column = sas_columns.get(key)
        python_column = python_columns.get(key)

        if sas_column is None or python_column is None:
            differences.append({
                "column": sas_column or python_column,
                "reason": "Column missing from one output",
                "sas": (
                    None if sas_column is None
                    else display_value(sas_row[sas_column])
                ),
                "python": (
                    None if python_column is None
                    else display_value(python_row[python_column])
                ),
            })
            continue

        sas_value = sas_row[sas_column]
        python_value = python_row[python_column]

        if not values_match(sas_value, python_value):
            differences.append({
                "column": python_column,
                "reason": "Value differs",
                "sas": display_value(sas_value),
                "python": display_value(python_value),
            })

    return {
        "program": program,
        "output": name,
        "status": "FAIL" if differences else "PASS",
        "difference_count": len(differences),
        "differences": differences,
    }


def compare_hospital(run_dir, provider_id):
    provider_id = str(provider_id).strip()

    if not provider_id:
        raise ValueError("Provider ID cannot be empty")

    checks = [
        compare_checkpoint(run_dir, provider_id, program, name)
        for program, name in CHECKPOINTS
    ]

    failures = {
        check["output"]: {
            "program": check["program"],
            "status": check["status"],
        }
        for check in checks
    }

    root = Path(__file__).resolve().parent.parent

    plans = {
        0: build_step_plan(root / "manifests/program0.json"),
        1: build_program1_plan(),
        2: build_step_plan(root / "manifests/program2.json"),
    }

    diagnostics = trace_validation_report(
        {"outputs": failures}, plans
    )

    return {
        "provider_id": provider_id,
        "checks": checks,
        "diagnostics": diagnostics,
        "note": (
            "The earliest mismatch is based on saved output "
            "checkpoints, not proof of the original faulty step."
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Compare a hospital against SAS outputs"
    )
    parser.add_argument("run_dir")
    parser.add_argument("provider_id")
    args = parser.parse_args()

    report = compare_hospital(args.run_dir, args.provider_id)

    print(f"\n=== HOSPITAL {report['provider_id']} ===")

    for check in report["checks"]:
        print(
            f"Program {check['program']} | "
            f"{check['output']}: {check['status']} "
            f"({check['difference_count']} differences)"
        )

        for difference in check["differences"][:3]:
            print(
                f"  {difference['column']}: "
                f"SAS={difference['sas']!r}, "
                f"Python={difference['python']!r}"
            )

    diagnostics = report["diagnostics"]
    stage = diagnostics["earliest_failing_stage"]

    print("\n=== FIRST OBSERVED MISMATCH ===")

    if stage is None:
        print("No differences found at the seven audited checkpoints.")
    else:
        print("Program:", stage)

        for name in diagnostics["earliest_failing_outputs"]:
            trace = diagnostics["failed_outputs"].get(name)

            print("Output:", name)

            if trace:
                print("Investigate first:", trace["investigate_first"])
                print("Downstream steps:", trace["downstream_steps"])

        if diagnostics["later_stage_failures"]:
            print(
                "Later failing outputs:",
                diagnostics["later_stage_failures"],
            )

    print("\n" + report["note"])


if __name__ == "__main__":
    main()
