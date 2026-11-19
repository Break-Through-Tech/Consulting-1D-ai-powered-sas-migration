df = datasets['WORK.OUTCOMES_MORTALITY'].copy()
measure_OM = ' '.join(df['measure_in_std'].dropna().astype(str))
outputs = {}
new_macro_vars = {'measure_OM': measure_OM}