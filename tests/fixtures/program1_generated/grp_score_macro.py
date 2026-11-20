import pandas as pd
import numpy as np
from typing import List

def grp_score(df: pd.DataFrame, varlist: List[str]) -> pd.DataFrame:
    df = df.copy()
    df['PROVIDER_ID'] = df['PROVIDER_ID'].astype(str)
    for var in varlist:
        df[var] = pd.to_numeric(df[var], errors='coerce')

    n = len(varlist)
    c_cols = [f'C{i}' for i in range(1, n + 1)]

    for i, var in enumerate(varlist):
        df[c_cols[i]] = df[var].notna().astype(int)

    df['total_cnt'] = df[c_cols].sum(axis=1)

    df['measure_wt'] = np.where(df['total_cnt'] > 0, 1.0 / df['total_cnt'], np.nan)

    sum_measures = df[varlist].sum(axis=1, skipna=True)
    df['score_before_std'] = np.where(df['total_cnt'] > 0, sum_measures * df['measure_wt'], np.nan)

    valid_scores = df['score_before_std'].dropna()
    mean_val = valid_scores.mean() if not valid_scores.empty else np.nan
    std_val = valid_scores.std(ddof=1) if len(valid_scores) > 1 else np.nan

    df['Mean'] = mean_val
    df['StdDev'] = std_val

    if pd.notna(std_val) and std_val != 0:
        df['grp_score'] = (df['score_before_std'] - mean_val) / std_val
    else:
        df['grp_score'] = np.where(df['score_before_std'].notna(), 0.0, np.nan)

    out_cols = ['PROVIDER_ID'] + varlist + c_cols + ['total_cnt', 'measure_wt', 'score_before_std', 'Mean', 'StdDev', 'grp_score']
    return df[out_cols]