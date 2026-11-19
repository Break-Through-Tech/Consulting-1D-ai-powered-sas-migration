def nation_avg(indata, out, var):
    df = indata.copy()
    cols_lower = {c.lower(): c for c in df.columns}
    tc_col = cols_lower.get('total_cnt', 'total_cnt')
    gs_col = cols_lower.get('grp_score', 'grp_score')
    pid_col = cols_lower.get('provider_id', 'provider_id')
    
    temp = df[df[tc_col] >= 3].copy()
    keep_cols = [pid_col, tc_col, gs_col]
    existing_keep = [c for c in keep_cols if c in temp.columns]
    temp = temp[existing_keep]
    
    mean_val = temp[gs_col].mean()
    out1 = pd.DataFrame({var: [mean_val]})
    
    result = out1[[var]].copy()
    return result