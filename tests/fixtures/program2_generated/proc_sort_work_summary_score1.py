df = datasets['WORK.SUMMARY_SCORE'].copy()
df = df.sort_values(by=['PROVIDER_ID'], kind='mergesort')
outputs = {'WORK.SUMMARY_SCORE1': df}