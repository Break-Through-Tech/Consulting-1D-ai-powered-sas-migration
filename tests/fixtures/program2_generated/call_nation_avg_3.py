df_in = datasets['OUTCOME_READMISSION']
res_df = nation_avg(indata=df_in, out='outcome_readmission_avg', var='Out_Readm_grp_Score_Nat')
outputs = {'WORK.OUTCOME_READMISSION_AVG': res_df}