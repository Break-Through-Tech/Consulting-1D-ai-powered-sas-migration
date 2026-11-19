df = datasets['WORK.STD_DATA_2025JUL'].copy()
measure_cnt = int(macro_vars['measure_cnt'])
measure_in = expand_name_list(macro_vars['measure_in'], df.columns)
measure_in_std = expand_name_list(macro_vars['measure_in_std'], df.columns)

for m_col, y_col in zip(measure_in, measure_in_std):
    df[y_col] = df[m_col]

df = df.drop(columns=measure_in)
outputs = {'STD_DATA_2025JUL_ANALYSIS': df}