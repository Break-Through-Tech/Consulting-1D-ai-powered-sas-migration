df = datasets['WORK.MEASURE_VOLUME_T'].copy()
df = df[(df['_NAME_'] != '_PAGE_') & (df['_NAME_'] != '_TABLE_')].copy()

df['measure_in_name'] = df['_NAME_'].str.replace('_N', '', regex=False)
df['measure_in_std'] = 'std_' + df['measure_in_name'].astype(str).str.strip()

valid_measures = [
    'HCP_COVID_19', 'IMM_3', 'OP_10', 'OP_13', 'OP_18B', 'OP_22', 
    'OP_23', 'OP_29', 'OP_8', 'PC_01', 'SAFE_USE_OF_OPIOIDS', 'SEP_1'
]

df = df[df['measure_in_name'].isin(valid_measures)].copy()
df = df[df['COL1'] > 100].copy()

outputs = {'WORK.PROCESS': df}