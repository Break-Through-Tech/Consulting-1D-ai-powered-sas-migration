"""
Compare Python-produced DataFrames against SAS reference outputs.

Checks:
1. Row counts
2. Missing and unexpected columns
3. Exact column order
4. Missing, blank, and duplicate key values
5. Row alignment by key
6. Cell values using explicit numeric tolerance
7. SAS-style text quirks such as padded text and blank = missing
"""

import numpy as np
import pandas as pd


DEFAULT_RTOL = 1e-6
DEFAULT_ATOL = 1e-12


def clean(df):
    """Normalize column names and text values before comparison."""
    df = df.copy()

    df.columns = [
        str(column).strip().upper()
        for column in df.columns
    ]

    for column in df.columns:
        if not pd.api.types.is_numeric_dtype(df[column]):
            values = df[column].astype("string").str.strip()
            df[column] = values.mask(values == "")

    return df


def _read_sas_csv(sas_csv, key):
    """
    Read a SAS reference CSV while preserving the key column as text.

    This prevents identifiers such as 001234 from being converted to
    integers and losing their leading zeros.
    """
    key_upper = key.strip().upper()

    header = pd.read_csv(sas_csv, nrows=0)

    key_column = next(
        (
            column
            for column in header.columns
            if str(column).strip().upper() == key_upper
        ),
        None,
    )

    dtype = (
        {key_column: "string"}
        if key_column is not None
        else None
    )

    return pd.read_csv(sas_csv, dtype=dtype)


def compare_detailed(
    py_df,
    sas_csv,
    key,
    rtol=DEFAULT_RTOL,
    atol=DEFAULT_ATOL,
):
    """
    Compare Python output against a SAS reference CSV.

    Returns structured validation details so callers such as the
    orchestration pipeline can determine why validation failed.
    """
    key = key.strip().upper()

    py = clean(py_df)
    sas = clean(_read_sas_csv(sas_csv, key))

    result = {
        "passed": False,
        "row_count_match": len(py) == len(sas),
        "python_rows": len(py),
        "sas_rows": len(sas),
        "column_order_match": list(py.columns) == list(sas.columns),
        "python_columns": list(py.columns),
        "sas_columns": list(sas.columns),
        "missing_columns": [],
        "extra_columns": [],
        "blank_python_keys": 0,
        "blank_sas_keys": 0,
        "duplicate_python_ids": [],
        "duplicate_sas_ids": [],
        "missing_ids": [],
        "extra_ids": [],
        "mismatches": {},
    }

    result["missing_columns"] = [
        column
        for column in sas.columns
        if column not in py.columns
    ]

    result["extra_columns"] = [
        column
        for column in py.columns
        if column not in sas.columns
    ]

    # We cannot safely align rows if the key column is missing.
    if key not in py.columns or key not in sas.columns:
        return result

    # Compare identifiers as text.
    py[key] = py[key].astype("string").str.strip()
    sas[key] = sas[key].astype("string").str.strip()

    # Treat blank strings as missing keys.
    py[key] = py[key].mask(py[key] == "")
    sas[key] = sas[key].mask(sas[key] == "")

    result["blank_python_keys"] = int(py[key].isna().sum())
    result["blank_sas_keys"] = int(sas[key].isna().sum())

    # Detect duplicate non-blank keys.
    python_keys = py.loc[py[key].notna(), key]
    sas_keys = sas.loc[sas[key].notna(), key]

    duplicate_python = (
        python_keys[
            python_keys.duplicated(keep=False)
        ]
        .unique()
        .tolist()
    )

    duplicate_sas = (
        sas_keys[
            sas_keys.duplicated(keep=False)
        ]
        .unique()
        .tolist()
    )

    result["duplicate_python_ids"] = sorted(duplicate_python)
    result["duplicate_sas_ids"] = sorted(duplicate_sas)

    python_id_set = set(python_keys.tolist())
    sas_id_set = set(sas_keys.tolist())

    result["missing_ids"] = sorted(
        sas_id_set - python_id_set
    )

    result["extra_ids"] = sorted(
        python_id_set - sas_id_set
    )

    # Blank or duplicate keys make one-to-one row comparison unsafe.
    if (
        result["blank_python_keys"]
        or result["blank_sas_keys"]
        or result["duplicate_python_ids"]
        or result["duplicate_sas_ids"]
    ):
        return result

    merged = py.merge(
        sas,
        on=key,
        how="inner",
        suffixes=("_py", "_sas"),
        validate="one_to_one",
    )

    common_columns = [
        column
        for column in sas.columns
        if column != key and column in py.columns
    ]

    for column in common_columns:
        py_values = merged[f"{column}_py"]
        sas_values = merged[f"{column}_sas"]

        if (
            pd.api.types.is_numeric_dtype(py_values)
            and pd.api.types.is_numeric_dtype(sas_values)
        ):
            same = np.isclose(
                py_values.to_numpy(dtype=float),
                sas_values.to_numpy(dtype=float),
                rtol=rtol,
                atol=atol,
                equal_nan=True,
            )
        else:
            same = (
                py_values.eq(sas_values)
                | (
                    py_values.isna()
                    & sas_values.isna()
                )
            ).fillna(False).to_numpy(dtype=bool)

        mismatch_count = int((~same).sum())

        if mismatch_count:
            examples = merged.loc[
                ~same,
                [
                    key,
                    f"{column}_py",
                    f"{column}_sas",
                ],
            ].head(5)

            result["mismatches"][column] = {
                "count": mismatch_count,
                "examples": examples.to_dict("records"),
            }

    result["passed"] = (
        result["row_count_match"]
        and result["column_order_match"]
        and not result["missing_columns"]
        and not result["extra_columns"]
        and not result["blank_python_keys"]
        and not result["blank_sas_keys"]
        and not result["duplicate_python_ids"]
        and not result["duplicate_sas_ids"]
        and not result["missing_ids"]
        and not result["extra_ids"]
        and not result["mismatches"]
    )

    return result


def compare(
    py_df,
    sas_csv,
    key,
    tol=DEFAULT_RTOL,
    atol=DEFAULT_ATOL,
):
    """
    Print a readable validation report and return True or False.

    The original compare() interface is preserved. The existing `tol`
    argument is treated as the relative tolerance required by the project.
    """
    result = compare_detailed(
        py_df,
        sas_csv,
        key,
        rtol=tol,
        atol=atol,
    )

    print(
        f"Rows: python={result['python_rows']}, "
        f"sas={result['sas_rows']}"
    )

    if not result["column_order_match"]:
        print("Column order does not match SAS output.")

    if result["missing_columns"]:
        print(
            "Missing columns:",
            result["missing_columns"],
        )

    if result["extra_columns"]:
        print(
            "Unexpected columns:",
            result["extra_columns"],
        )

    if result["blank_python_keys"]:
        print(
            "Blank Python keys:",
            result["blank_python_keys"],
        )

    if result["blank_sas_keys"]:
        print(
            "Blank SAS keys:",
            result["blank_sas_keys"],
        )

    if result["duplicate_python_ids"]:
        print(
            "Duplicate Python IDs:",
            result["duplicate_python_ids"],
        )

    if result["duplicate_sas_ids"]:
        print(
            "Duplicate SAS IDs:",
            result["duplicate_sas_ids"],
        )

    if result["missing_ids"]:
        print(
            "Missing Python IDs:",
            result["missing_ids"],
        )

    if result["extra_ids"]:
        print(
            "Unexpected Python IDs:",
            result["extra_ids"],
        )

    for column, mismatch in result["mismatches"].items():
        print(
            f"\n{column}: "
            f"{mismatch['count']} mismatches"
        )

        if mismatch["examples"]:
            print(
                pd.DataFrame(
                    mismatch["examples"]
                )
            )

    print(
        "\nPASS"
        if result["passed"]
        else "\nFAIL"
    )

    return result["passed"]
