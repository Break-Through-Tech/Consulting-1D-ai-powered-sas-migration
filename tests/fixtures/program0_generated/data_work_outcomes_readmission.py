df = datasets['WORK.MEASURE_VOLUME_T'].copy()
df = df[~df['_NAME_'].isin(['_PAGE_', '_TABLE_'])].copy()
df['measure_in_name'] = df['_NAME_'].str.replace('_N', '', regex=False)
df['measure_in_std'] = 'std_' + df['measure_in_name'].astype(str).str.strip()
readmission_list = ['EDAC_30_AMI', 'EDAC_30_HF', 'EDAC_30_PN', 'OP_32', 'READM_30_CABG', 'READM_30_COPD', 'READM_30_HIP_KNEE', 'READM_30_HOSP_WIDE', 'OP_35_ADM', 'OP_35_ED', 'OP_36']
df = df[df['measure_in_name'].isin(readmission_list)].copy()
df = df[df['COL1'] > 100].copy()
outputs = {'WORK.OUTCOMES_READMISSION': df}