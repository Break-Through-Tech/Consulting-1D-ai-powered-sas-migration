def nation_avg0(indata, out, var):
    df = indata.copy()
    temp = df[df['report_indicator'] == 1][['PROVIDER_ID', 'summary_score']].copy()
    mean_val = temp['summary_score'].mean()
    out1 = pd.DataFrame({var: [mean_val]})
    out_df = out1[[var]].copy()
    return out_df