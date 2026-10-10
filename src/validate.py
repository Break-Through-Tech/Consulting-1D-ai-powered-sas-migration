"""
validate.py purpose is to compare a python produced dataframe against a sas given output.

checks performed: 
1. row counts
2. missing columns
3. row alignment by key column
4. column values ( with a specific tolerance for value differences and sas style quirks(e.g. case insensitive column names, padded text, blank = missing))

"""
import numpy as np
import pandas as pd


def clean(df):
    df = df.copy()
    df.columns = [c.strip().upper() for c in df.columns]
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].astype(str).str.strip().replace(["", "nan"], np.nan)
    return df


def compare(py_df, sas_csv, key, tol=1e-6):
    py = clean(py_df)
    sas = clean(pd.read_csv(sas_csv))
    key = key.upper()
    ok = True

    print(f"Rows: python={len(py)}, sas={len(sas)}")
    if len(py) != len(sas):
        ok = False

    missing = [c for c in sas.columns if c not in py.columns]
    if missing:
        print("Missing columns:", missing)
        ok = False

    py[key] = py[key].astype(str)
    sas[key] = sas[key].astype(str)
    merged = py.merge(sas, on=key, suffixes=("_py", "_sas"))
    if len(merged) != len(sas):
        print(f"Unmatched IDs: {len(sas) - len(merged)} SAS rows have no match in python")
        ok = False
    
    for col in sas.columns:
        if col == key or col in missing:
            continue
        a, b = merged[col + "_py"], merged[col + "_sas"]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            same = np.isclose(a, b, atol=tol) | (a.isna() & b.isna())
        else:
            same = (a == b) | (a.isna() & b.isna())
        if not same.all():
            ok = False
            print(f"\n{col}: {(~same).sum()} mismatches")
            print(merged.loc[~same, [key, col + "_py", col + "_sas"]].head())

    print("\nPASS" if ok else "\nFAIL")
    return ok
