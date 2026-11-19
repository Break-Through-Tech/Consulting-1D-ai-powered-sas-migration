df = datasets['WORK.INCLUDE_MEASURE']
names = df['measure_in_name'].dropna().astype(str).tolist()
measure_in_val = ' '.join(names)
outputs = {}
new_macro_vars = {'measure_in': measure_in_val}