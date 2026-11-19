df = datasets['WORK.PTEXP'].copy()
measure_pt_exp = ' '.join(df['measure_in_std'].dropna().astype(str))
outputs = {}
new_macro_vars = {'measure_PtExp': measure_pt_exp}