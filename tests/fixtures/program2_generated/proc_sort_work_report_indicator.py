df = datasets['WORK.REPORT_INDICATOR'].copy()
df = df.sort_values(by=['PROVIDER_ID']).reset_index(drop=True)
outputs = {'WORK.REPORT_INDICATOR': df}