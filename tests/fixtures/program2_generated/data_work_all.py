df_om = datasets['OUTCOME_MORTALITY'][['PROVIDER_ID', 'grp_score']].copy().rename(columns={'grp_score': 'Std_Outcomes_Mortality_score'}) 

df_or = datasets['OUTCOME_READMISSION'][['PROVIDER_ID', 'grp_score']].copy().rename(columns={'grp_score': 'Std_Outcomes_Readmission_score'}) 

df_os = datasets['OUTCOME_SAFETY'][['PROVIDER_ID', 'grp_score']].copy().rename(columns={'grp_score': 'Std_Outcomes_Safety_score'}) 

df_pt = datasets['PTEXP'][['PROVIDER_ID', 'grp_score']].copy().rename(columns={'grp_score': 'Std_PatientExp_score'}) 

df_pr = datasets['PROCESS'][['PROVIDER_ID', 'grp_score']].copy().rename(columns={'grp_score': 'Std_Process_score'}) 

merged = df_om.merge(df_or, on='PROVIDER_ID', how='outer') 
merged = merged.merge(df_os, on='PROVIDER_ID', how='outer') 
merged = merged.merge(df_pt, on='PROVIDER_ID', how='outer') 
merged = merged.merge(df_pr, on='PROVIDER_ID', how='outer') 

outputs = {'WORK.ALL': merged}