df_in = datasets['OUTCOME_SAFETY']
res_df = nation_avg(df_in, 'WORK.OUTCOME_SAFETY_AVG', 'Out_Sft_Grp_Score_Nat')
outputs = {'WORK.OUTCOME_SAFETY_AVG': res_df}