df = datasets['WORK.SUMMARY_SCORE22'].copy()
mask = df['cnt_grp'].astype(str).str.rstrip() == '3) # of groups=5'
filtered_df = df[mask].reset_index(drop=True)
outputs = {'WORK.CNT_S30': filtered_df}