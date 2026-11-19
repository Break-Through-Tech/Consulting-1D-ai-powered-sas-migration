df = datasets['WORK.CNT_S_STR'].copy()
df = df.sort_values(by='PROVIDER_ID')
outputs = {'WORK.CNT_S_STR': df}