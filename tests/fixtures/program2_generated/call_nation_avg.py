df_in = datasets['OUTCOME_MORTALITY']
df_out = nation_avg(df_in, 'WORK.OUTCOME_MORTALITY_AVG', 'Out_Mrt_Grp_Score_Nat')
outputs = {'WORK.OUTCOME_MORTALITY_AVG': df_out}