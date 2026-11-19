df = datasets['WORK.SUMMARY_SCORE22'].copy()
df = df[df['cnt_grp'] == '2) # of groups=4       ']
outputs = {'WORK.CNT_S20': df}