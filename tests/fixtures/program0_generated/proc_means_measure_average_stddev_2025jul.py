df = datasets['WORK.INITIAL_DATA_2025JUL'].copy()
measure_vars = expand_name_list(macro_vars['measure_in'], df.columns)
stats = ['N', 'MIN', 'MAX', 'MEAN', 'STD']
result_rows = []
n_freq = len(df)
for stat in stats:
    row = {'_TYPE_': 0, '_FREQ_': n_freq, '_STAT_': stat}
    for v in measure_vars:
        s = df[v]
        if stat == 'N':
            val = s.notnull().sum()
        elif stat == 'MIN':
            val = s.min()
        elif stat == 'MAX':
            val = s.max()
        elif stat == 'MEAN':
            val = s.mean()
        elif stat == 'STD':
            val = s.std(ddof=1)
        row[v] = val
    result_rows.append(row)
out_df = pd.DataFrame(result_rows)
outputs = {'MEASURE_AVERAGE_STDDEV_2025JUL': out_df}