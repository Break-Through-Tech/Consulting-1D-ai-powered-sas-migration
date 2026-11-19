df = datasets['WORK.MEASURE_VOLUME_T'].copy()
df = df[(df['_NAME_'] != '_PAGE_') & (df['_NAME_'] != '_TABLE_')].copy()
df['measure_in_name'] = df['_NAME_'].str.replace('_N', '', regex=False)
df['measure_in_std'] = 'std_' + df['measure_in_name'].astype(str).str.strip()
subset_measures = ['COMP_HIP_KNEE', 'HAI_1', 'HAI_2', 'HAI_3', 'HAI_4', 'HAI_5', 'HAI_6', 'PSI_90_SAFETY']
df = df[df['measure_in_name'].isin(subset_measures)].copy()
df = df[df['COL1'] > 100].copy()
outputs = {'WORK.OUTCOMES_SAFETY': df}