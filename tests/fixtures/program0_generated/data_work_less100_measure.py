df = datasets['WORK.MEASURE_VOLUME_T'].copy()
df = df[df['COL1'] <= 100].copy()
df = df[~df['_NAME_'].isin(['_PAGE_', '_TABLE_'])].copy()
df['measure_name'] = df['_NAME_'].str.replace('_N', '', regex=False)
df = df.drop(columns=['_NAME_', '_LABEL_'])
df = df.rename(columns={'COL1': 'freq'})
outputs = {'WORK.LESS100_MEASURE': df}