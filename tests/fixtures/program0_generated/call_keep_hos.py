indsn = datasets['WORK.INITIAL_DATA_2025JUL']
measure_in = macro_vars['measure_in']
measure_cnt = int(macro_vars['measure_cnt'])
res_df = keep_hos(indsn, measure_in, measure_cnt)
outputs = {'WORK.INITIAL_DATA_2025JUL': res_df}