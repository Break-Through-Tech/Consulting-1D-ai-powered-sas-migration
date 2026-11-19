df = datasets['WORK.OUTCOMES_SAFETY']
names = df['measure_in_std'].dropna().astype(str).tolist()
measure_OS = ' '.join(names)
new_macro_vars = {'measure_OS': measure_OS}
outputs = {}