# Original rounded CSVs

These are the output CSVs we were first given. They are rounded, so correct code fails against them. See issue #40.

`Mean` and `StdDev` have 6 decimals, except `Mean` in `PTEXP.csv`, which is stored as `7.533033E-17`. Other non-integer values were cut to fit 12 characters, so they have at most 10 decimals, and fewer for negative or larger values.

The CSVs in `SAS Output CSV/` replaced them. Those files hold the exact values from the `.sas7bdat` files, and were made with:

```
python -m src.export_exact_csvs <new_folder>
```

Then the 10 output CSVs were copied into `SAS Output CSV/`. `Starrating/alldata_2025jul.csv` was not replaced, because it differs from its `.sas7bdat` file by at most `1.1e-16`.

Nothing in the code reads this folder. We keep these files for reference.
