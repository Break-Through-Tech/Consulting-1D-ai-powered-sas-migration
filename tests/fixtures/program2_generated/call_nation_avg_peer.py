indata_df = datasets.get('STAR_2025JUL')
res = nation_avg_peer(indata_df, 'summary_avg_peer', 'Summary_Score_Nat', 'Total_measure_group_cnt')
outputs = {'WORK.SUMMARY_AVG_PEER': res}