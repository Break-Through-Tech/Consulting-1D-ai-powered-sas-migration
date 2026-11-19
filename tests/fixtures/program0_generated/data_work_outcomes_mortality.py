df = datasets['WORK.MEASURE_VOLUME_T'].copy()
df = df[(df['_NAME_'] != '_PAGE_') & (df['_NAME_'] != '_TABLE_')].copy()
df['measure_in_name'] = df['_NAME_'].str.replace('_N', '', regex=False)
df['measure_in_std'] = 'std_' + df['measure_in_name'].astype(str).str.strip()

mort_measures = [
    'MORT_30_AMI',
    'MORT_30_CABG',
    'MORT_30_COPD',
    'MORT_30_HF',
    'MORT_30_PN',
    'MORT_30_STK',
    'PSI_4_SURG_COMP'
]

df = df[df['measure_in_name'].isin(mort_measures)].copy()
df = df[df['COL1'] > 100].copy()

outputs = {'WORK.OUTCOMES_MORTALITY': df}