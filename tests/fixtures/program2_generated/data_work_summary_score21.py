df1 = datasets['WORK.SUMMARY_SCORE1'].sort_values('PROVIDER_ID').reset_index(drop=True)
df2 = datasets['WORK.REPORT_INDICATOR'].sort_values('PROVIDER_ID').reset_index(drop=True)
res = pd.merge(df1, df2, on='PROVIDER_ID', how='outer')
outputs = {'WORK.SUMMARY_SCORE21': res}