df = datasets['WORK.LESS100_MEASURE']
names = df['measure_Name'].dropna().astype(str).tolist()
measure_exclude = ' '.join(names)
outputs = {}
new_macro_vars = {'measure_exclude': measure_exclude}