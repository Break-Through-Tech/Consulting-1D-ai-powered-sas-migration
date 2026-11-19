df = datasets['WORK.PROCESS'].copy()
measure_names = df['measure_in_std'].dropna().astype(str).tolist()
measure_Process = ' '.join(measure_names)
outputs = {}
new_macro_vars = {'measure_Process': measure_Process}