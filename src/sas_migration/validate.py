"""Compare a Python-produced dataset against its SAS ground-truth output.

Checks row count, column names and order, missing-value patterns, and
values (numeric within tolerance, strings exactly after SAS blank-padding
is stripped).
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

KEY = "PROVIDER_ID"
RTOL = 1e-9
ATOL = 1e-9


@dataclass
class ColumnDiff:
    column: str
    n_missing_mismatch: int  # missing in one dataset but not the other
    n_value_mismatch: int  # both present, values differ beyond tolerance
    max_abs_diff: float = 0.0  # numeric columns only

    @property
    def passed(self) -> bool:
        return self.n_missing_mismatch == 0 and self.n_value_mismatch == 0


@dataclass
class ComparisonReport:
    dataset: str
    python_rows: int
    sas_rows: int
    missing_columns: list[str]  # in SAS output, not in Python output
    extra_columns: list[str]  # in Python output, not in SAS output
    column_order_matches: bool
    key_mismatch: int = 0  # rows whose key is not in the other dataset
    column_diffs: list[ColumnDiff] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.python_rows == self.sas_rows
            and not self.missing_columns
            and not self.extra_columns
            and self.column_order_matches
            and self.key_mismatch == 0
            and all(d.passed for d in self.column_diffs)
        )

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [f"[{status}] {self.dataset}: rows python={self.python_rows} sas={self.sas_rows}, "
                 f"{len(self.column_diffs)} columns compared"]
        if self.missing_columns:
            lines.append(f"  missing columns: {self.missing_columns}")
        if self.extra_columns:
            lines.append(f"  extra columns: {self.extra_columns}")
        if not self.column_order_matches:
            lines.append("  column order differs")
        if self.key_mismatch:
            lines.append(f"  {self.key_mismatch} {KEY} values not in both datasets")
        for d in self.column_diffs:
            if not d.passed:
                lines.append(f"  {d.column}: {d.n_missing_mismatch} missing-pattern mismatches, "
                             f"{d.n_value_mismatch} value mismatches (max abs diff {d.max_abs_diff:.3g})")
        max_diff = max((d.max_abs_diff for d in self.column_diffs), default=0.0)
        lines.append(f"  max abs numeric diff: {max_diff:.3g}")
        return "\n".join(lines)


def _normalize_str(s: pd.Series) -> pd.Series:
    # SAS stores missing character values as blanks and pads with trailing spaces.
    return s.astype(object).where(s.notna(), "").astype(str).str.rstrip()


def _compare_column(name: str, py: pd.Series, sas: pd.Series, rtol: float, atol: float) -> ColumnDiff:
    if pd.api.types.is_numeric_dtype(sas) and pd.api.types.is_numeric_dtype(py):
        a, b = py.to_numpy(float), sas.to_numpy(float)
        a_na, b_na = np.isnan(a), np.isnan(b)
        both = ~a_na & ~b_na
        diff = np.abs(a[both] - b[both])
        close = np.isclose(a[both], b[both], rtol=rtol, atol=atol)
        return ColumnDiff(name, int((a_na != b_na).sum()), int((~close).sum()),
                          float(diff.max()) if diff.size else 0.0)
    a, b = _normalize_str(py).to_numpy(), _normalize_str(sas).to_numpy()
    return ColumnDiff(name, 0, int((a != b).sum()))


def compare_datasets(python_df: pd.DataFrame, sas_df: pd.DataFrame, dataset: str,
                     rtol: float = RTOL, atol: float = ATOL) -> ComparisonReport:
    """Compare two datasets. Rows are matched on PROVIDER_ID when both have it, else by position."""
    report = ComparisonReport(
        dataset=dataset,
        python_rows=len(python_df),
        sas_rows=len(sas_df),
        missing_columns=[c for c in sas_df.columns if c not in python_df.columns],
        extra_columns=[c for c in python_df.columns if c not in sas_df.columns],
        column_order_matches=list(python_df.columns) == list(sas_df.columns),
    )

    py, sas = python_df.reset_index(drop=True), sas_df.reset_index(drop=True)
    if KEY in py.columns and KEY in sas.columns:
        py_keys, sas_keys = set(py[KEY]), set(sas[KEY])
        report.key_mismatch = len(py_keys ^ sas_keys)
        shared = sorted(py_keys & sas_keys)
        py = py.set_index(KEY).loc[shared].reset_index()
        sas = sas.set_index(KEY).loc[shared].reset_index()
    elif len(py) != len(sas):
        return report  # can't align rows positionally

    for col in sas.columns:
        if col in py.columns:
            report.column_diffs.append(_compare_column(col, py[col], sas[col], rtol, atol))
    return report
