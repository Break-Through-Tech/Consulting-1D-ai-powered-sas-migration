df = datasets['WORK.INCLUDE_MEASURE']
measure_in_std = ' '.join(df['measure_in_std'].dropna().astype(str))
new_macro_vars = {'measure_in_std': measure_in_std}
outputs = {}