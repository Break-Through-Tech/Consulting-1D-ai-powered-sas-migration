df_sum = datasets['WORK.SUMMARY_AVG'].copy()
df_sum_peer = datasets['WORK.SUMMARY_AVG_PEER'].copy()
df_om = datasets['WORK.OUTCOME_MORTALITY_AVG'].copy()
df_os = datasets['WORK.OUTCOME_SAFETY_AVG'].copy()
df_or = datasets['WORK.OUTCOME_READMISSION_AVG'].copy()
df_pt = datasets['WORK.PTEXP_AVG'].copy()
df_pr = datasets['WORK.PROCESS_AVG'].copy()
res = df_sum
for d in [df_sum_peer, df_om, df_os, df_or, df_pt, df_pr]:
    res = pd.merge(res, d, how='outer', left_index=True, right_index=True)
outputs = {'NATIONAL_AVERAGE_2025JUL': res}