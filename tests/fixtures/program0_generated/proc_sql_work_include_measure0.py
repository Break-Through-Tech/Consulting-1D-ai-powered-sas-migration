df = datasets['WORK.INCLUDE_MEASURE0']
names = df['measure_in_name'].astype(str).tolist()
measure_in_val = ' '.join(names)
new_macro_vars = {'measure_in': measure_in_val}
outputs = {}