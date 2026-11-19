df = datasets['WORK.INITIAL_DATA_2025JUL'].copy()
measure_vars = expand_name_list(macro_vars['measure_in'], df.columns)
result_df = df.copy()
for col in measure_vars:
    if col in result_df.columns:
        s = pd.to_numeric(result_df[col], errors='coerce')
        mean_val = s.mean()
        std_val = s.std(ddof=1)
        if std_val == 0 or pd.isna(std_val):
            result_df[col] = 0.0
        else:
            result_df[col] = (s - mean_val) / std_val
outputs = {'WORK.STD_DATA_2025JUL': result_df}