def nation_avg_peer(indata, out, var, gr):
    df = indata.copy()
    df_filtered = df[df['report_indicator'] == 1].copy()
    keep_cols = ['PROVIDER_ID', 'summary_score', gr]
    df_temp = df_filtered[[c for c in keep_cols if c in df_filtered.columns]].copy()
    
    out1 = df_temp.groupby(gr, as_index=False)['summary_score'].mean()
    out1 = out1.rename(columns={'summary_score': var})
    
    out2 = out1.pivot_table(columns=gr, values=var).reset_index(drop=True)
    out2.columns = [f"{var}_peer{int(c)}" for c in out2.columns]
    
    out_df = out2.copy()
    if '_name_' in out_df.columns:
        out_df = out_df.drop(columns=['_name_'])
    
    return out_df