df = datasets['WORK.OUTCOMES_READMISSION'].copy()
measures = df['measure_in_std'].dropna().astype(str).tolist()
measure_OR = ' '.join(measures)
outputs = {}
new_macro_vars = {'measure_OR': measure_OR}