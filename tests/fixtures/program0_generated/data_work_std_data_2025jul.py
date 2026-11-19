df = datasets['WORK.STD_DATA_2025JUL'].copy()
flip_cols = [
    'MORT_30_AMI', 'MORT_30_CABG', 'MORT_30_COPD', 'MORT_30_HF', 'MORT_30_PN', 'MORT_30_STK', 'PSI_4_SURG_COMP',
    'COMP_HIP_KNEE', 'HAI_1', 'HAI_2', 'HAI_3', 'HAI_4', 'HAI_5', 'HAI_6', 'PSI_90_SAFETY',
    'EDAC_30_AMI', 'EDAC_30_HF', 'EDAC_30_PN', 'OP_32', 'READM_30_CABG', 'READM_30_COPD',
    'READM_30_HIP_KNEE', 'READM_30_HOSP_WIDE', 'OP_35_ADM', 'OP_35_ED', 'OP_36',
    'OP_22', 'PC_01', 'OP_18B',
    'OP_8', 'OP_10', 'OP_13', 'SAFE_USE_OF_OPIOIDS'
]
actual_cols = expand_name_list(' '.join(flip_cols), df.columns)
for col in actual_cols:
    if col in df.columns:
        df[col] = -df[col]
outputs = {'WORK.STD_DATA_2025JUL': df}