df1 = datasets['WORK.CNT_S1']
df2 = datasets['WORK.CNT_S2']
df3 = datasets['WORK.CNT_S3']
out_df = pd.concat([df1, df2, df3], ignore_index=True)
outputs = {'WORK.CNT_S_STR': out_df}