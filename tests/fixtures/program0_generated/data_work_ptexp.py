df = datasets['WORK.MEASURE_VOLUME_T'].copy()
df = df[(df['_NAME_'] != '_PAGE_') & (df['_NAME_'] != '_TABLE_')].copy()
df['measure_in_name'] = df['_NAME_'].str.replace('_N', '', regex=False)
df['measure_in_std'] = 'std_' + df['measure_in_name'].astype(str).str.strip()
measures = ['H_COMP_1_STAR_RATING', 'H_COMP_2_STAR_RATING', 'H_COMP_3_STAR_RATING', 'H_COMP_5_STAR_RATING', 'H_COMP_6_STAR_RATING', 'H_COMP_7_STAR_RATING', 'H_GLOB_STAR_RATING', 'H_INDI_STAR_RATING']
df = df[df['measure_in_name'].isin(measures)].copy()
df = df[df['COL1'] > 100].copy()
outputs = {'WORK.PTEXP': df}