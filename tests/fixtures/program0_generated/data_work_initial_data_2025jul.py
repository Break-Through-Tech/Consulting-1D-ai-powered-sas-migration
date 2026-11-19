df = datasets['WORK.ALL_DATA_2025JUL'].copy()
nobs = int(macro_vars['NOBS'])
if nobs > 0:
    measure_exclude = macro_vars['measure_exclude']
    drop_cols = expand_name_list(measure_exclude, df.columns)
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])
outputs = {'WORK.INITIAL_DATA_2025JUL': df}