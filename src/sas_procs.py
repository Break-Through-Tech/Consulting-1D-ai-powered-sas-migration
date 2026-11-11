"""Hand-written versions of SAS procedures that have no exact Python equivalent.

Translated code calls these instead of approximating the procedure with another
library. For example, scikit-learn's KMeans rates 2 of 2,891 Program 2 hospitals
differently from PROC FASTCLUS even when given SAS's seeds, because it has no
STRICT= option.

Each function covers only the options the SAS programs use. Anything else raises
NotImplementedError, so a translation cannot silently behave differently from SAS.

  - percentile, univariate_percentiles: PROC UNIVARIATE ... OUTPUT PCTLPTS=
    (the default percentile definition, PCTLDEF=5)
  - fastclus: PROC FASTCLUS, as %kmeans in Star_Macros.sas uses it

Checked against the SAS outputs by tests/test_sas_procs.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def percentile(values, pct: int) -> float:
    """PROC UNIVARIATE's default percentile (PCTLDEF=5) of the non-missing values."""
    if isinstance(pct, bool) or not isinstance(pct, (int, np.integer)) or not 0 <= pct <= 100:
        raise NotImplementedError(f"Only whole-number percentiles from 0 to 100 are supported, got {pct!r}.")

    # Missing values are left out, as in SAS.
    ordered = np.sort(pd.Series(values, dtype=float).dropna().to_numpy())
    if len(ordered) == 0:
        raise ValueError("No non-missing values.")

    if pct == 0:
        return float(ordered[0])
    if pct == 100:
        return float(ordered[-1])

    # With n*p/100 = j + g, take the (j+1)th smallest value, or average the jth and
    # (j+1)th when g = 0. Integer arithmetic keeps g exact (2235 * 20 / 100 must be exactly 447).
    j, g = divmod(len(ordered) * int(pct), 100)
    return float((ordered[j - 1] + ordered[j]) / 2 if g == 0 else ordered[j])


def univariate_percentiles(data: pd.DataFrame, var: str, pctlpts, pctlpre: str = "P") -> pd.DataFrame:
    """PROC UNIVARIATE; VAR var; OUTPUT OUT= PCTLPRE= PCTLPTS=;

    Returns the OUT= data set: one row, one column per percentile. For example,
    pctlpts=range(20, 101, 20) gives the columns P20, P40, P60, P80 and P100.
    """
    return pd.DataFrame([{f"{pctlpre}{pct}": percentile(data[var], pct) for pct in pctlpts}])


@dataclass
class FastclusResult:
    """The parts of PROC FASTCLUS's output that %kmeans uses."""

    # ODS ClusterCenters: a Cluster column (1, 2, ...) and the final center in a
    # column named after the VAR. SAS stores Cluster as text here, which is why
    # %kmeans converts it with input(cluster, 4.); this one is already a number.
    cluster_centers: pd.DataFrame

    # OUT=: the input rows, in their original order, plus CLUSTER and DISTANCE
    # (the distance to the nearest final center). With STRICT=, a row farther than
    # STRICT from every center gets the negative of its nearest cluster's number.
    out: pd.DataFrame

    # ODS ConvergenceStatus: whether the centers stopped changing within MAXITER.
    converged: bool


def fastclus(data: pd.DataFrame, var: str, seed: pd.DataFrame, *, maxc: int, maxiter: int = 1,
             converge: float = 0.02, strict: float | None = None) -> FastclusResult:
    """PROC FASTCLUS DATA=data SEED=seed MAXC= MAXITER= CONVERGE= STRICT=; VAR var;

    The defaults are SAS's, so leaving an option out either behaves as SAS would
    or raises NotImplementedError.

    Supported, which is everything %kmeans uses:
      - one VAR, with no missing values
      - a SEED= data set with exactly MAXC distinct seeds, read from its VAR column
        in row order
      - CONVERGE=0: iterate until the centers stop changing
      - STRICT=

    Each pass assigns every row to its nearest center, then moves each center to
    the mean of its rows. With STRICT=, rows farther than that from their nearest
    center are left out of the means, so an outlier cannot drag a cluster.

    REPLACE=FULL, the default, needs no code. With exactly MAXC distinct seeds there
    is no spare seed to swap in. This is inferred from the SAS documentation and
    output, not from SAS's source: all 6 FASTCLUS runs in "SAS Output.htm" print
    their seeds unchanged.

    Not verified against SAS, because they never happen in the 2025 Jul data:
      - a cluster with no rows, which keeps its previous center here
      - a row exactly as far from two centers, which goes to the lower-numbered one
    """
    if not isinstance(var, str):
        raise NotImplementedError("Only one VAR is supported.")
    if converge != 0:
        raise NotImplementedError(f"Only CONVERGE=0 is supported, got {converge!r}.")
    if not isinstance(maxiter, (int, np.integer)) or maxiter < 1:
        raise NotImplementedError(f"MAXITER must be a whole number of at least 1, got {maxiter!r}.")
    if strict is not None and not strict > 0:
        raise NotImplementedError(f"STRICT must be positive, got {strict!r}.")

    values = data[var].to_numpy(dtype=float)
    if np.isnan(values).any():
        raise NotImplementedError(f"Missing values in {var} are not supported.")

    centers = seed[var].to_numpy(dtype=float)
    if len(centers) != maxc:
        raise NotImplementedError(f"SEED= must have exactly MAXC={maxc} seeds, got {len(centers)}.")
    if np.isnan(centers).any() or len(np.unique(centers)) != len(centers):
        raise NotImplementedError("Missing or equal seeds are not supported.")

    converged = False
    for _ in range(maxiter):
        distances = np.abs(values[:, np.newaxis] - centers[np.newaxis, :])
        nearest = distances.argmin(axis=1)

        new_centers = centers.copy()
        for k in range(len(centers)):
            members = nearest == k
            if strict is not None:
                members &= distances[:, k] <= strict
            if members.any():
                new_centers[k] = values[members].mean()

        converged = np.array_equal(new_centers, centers)
        centers = new_centers
        if converged:
            break

    # OUT= uses the final centers.
    distances = np.abs(values[:, np.newaxis] - centers[np.newaxis, :])
    nearest = distances.argmin(axis=1)
    distance = distances[np.arange(len(values)), nearest]
    cluster = nearest + 1
    if strict is not None:
        cluster = np.where(distance > strict, -cluster, cluster)

    out = data.copy()
    out["CLUSTER"] = cluster
    out["DISTANCE"] = distance

    return FastclusResult(
        cluster_centers=pd.DataFrame({"Cluster": np.arange(1, len(centers) + 1), var: centers}),
        out=out,
        converged=bool(converged),
    )
