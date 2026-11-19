df = datasets['WORK.MEASURE_VOLUME_T0'].copy()
df = df[~df['_NAME_'].isin(['_PAGE_', '_TABLE_'])].copy()
df['measure_in_name'] = df['_NAME_'].str.replace('_N', '', regex=False)
df['measure_in_std'] = 'std_' + df['measure_in_name'].str.strip()
drop_cols = ['_NAME_', '_LABEL_']
df = df.drop(columns=[c for c in drop_cols if c in df.columns])
df = df.rename(columns={'COL1': 'freq'})
outputs = {'WORK.INCLUDE_MEASURE0': df}