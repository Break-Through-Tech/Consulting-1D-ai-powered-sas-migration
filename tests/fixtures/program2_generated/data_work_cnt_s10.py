df = datasets['WORK.SUMMARY_SCORE22'].copy()
df = df[df['cnt_grp'] == '1) # of groups=3       ']
outputs = {'WORK.CNT_S10': df}